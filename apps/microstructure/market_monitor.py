from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Callable

import httpx
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.market_data.models import (
    DeribitVolatilityIndexCandle,
    FundingRate,
    OpenInterest,
)

from .market_pilot import (
    PROMPT_VERSION,
    FourHourBlock,
    _market_evidence,
    _news_evidence,
    analyze_with_deepseek,
)
from .models import (
    MarketMinute,
    MarketPilotReport,
    MarketPilotRun,
    MarketPilotWindowCheck,
)


LIVE_LIMITATIONS = [
    "实时检测由完整的分钟 K 线构造，不依赖可能滞后的日频一小时采集任务。",
    "数据库当前不能独立验证 BTC 与全市场同步性。",
    "ETF 资金流和地址余额不是四小时实时数据，不能作为即时触发证据。",
]
OUTCOME_HORIZONS = (4, 12, 24)
MAX_NOTIFICATION_ATTEMPTS = 3


class MarketMonitorAlreadyRunning(RuntimeError):
    pass


def latest_closed_window_end(now: datetime | None = None) -> datetime:
    current = now or timezone.now()
    if timezone.is_naive(current):
        raise ValueError("now must be timezone-aware")
    current = current.astimezone(UTC)
    return current.replace(
        hour=current.hour - current.hour % 4,
        minute=0,
        second=0,
        microsecond=0,
    )


def _minute_block(
    symbol: str, start: datetime
) -> tuple[FourHourBlock | None, dict[str, object]]:
    end = start + timedelta(hours=4)
    rows = list(
        MarketMinute.objects.filter(
            symbol=symbol,
            minute_start__gte=start,
            minute_start__lt=end,
        ).order_by("minute_start")
    )
    expected_times = {start + timedelta(minutes=index) for index in range(240)}
    actual_times = {row.minute_start.astimezone(UTC) for row in rows}
    missing_count = len(expected_times - actual_times)
    closed_count = sum(row.kline_closed for row in rows)
    price_complete = bool(
        rows
        and all(
            row.open_price is not None
            and row.high_price is not None
            and row.low_price is not None
            and row.close_price is not None
            for row in rows
        )
    )
    quality = {
        "minute_rows": len(rows),
        "closed_rows": closed_count,
        "missing_minutes": missing_count,
        "price_complete": price_complete,
    }
    if (
        len(rows) != 240
        or missing_count
        or closed_count != 240
        or not price_complete
    ):
        return None, quality

    return (
        FourHourBlock(
            start=start,
            end=end,
            open=rows[0].open_price,
            high=max(row.high_price for row in rows),
            low=min(row.low_price for row in rows),
            close=rows[-1].close_price,
            quote_volume=sum((row.quote_volume for row in rows), Decimal(0)),
            taker_buy_quote_volume=sum(
                (row.taker_buy_quote for row in rows), Decimal(0)
            ),
        ),
        quality,
    )


def _freshness_minutes(observed_at: datetime, row_at: datetime | None) -> int | None:
    if row_at is None:
        return None
    return max(0, int((observed_at - row_at).total_seconds() // 60))


def build_live_input(
    symbol: str, window_start: datetime
) -> tuple[dict[str, object] | None, dict[str, object]]:
    blocks: list[FourHourBlock] = []
    block_quality: list[dict[str, object]] = []
    for offset in range(-6, 1):
        block, quality = _minute_block(
            symbol, window_start + timedelta(hours=offset * 4)
        )
        block_quality.append(quality)
        if block is None:
            return None, {
                "ready": False,
                "reason": "四小时窗口或前 24 小时基线的分钟数据不完整。",
                "blocks": block_quality,
            }
        blocks.append(block)

    block = blocks[-1]
    oi = (
        OpenInterest.objects.filter(
            symbol=symbol,
            period=OpenInterest.Period.FIVE_MINUTES,
            timestamp__lte=block.end,
        )
        .order_by("-timestamp")
        .first()
    )
    funding = (
        FundingRate.objects.filter(symbol=symbol, funding_time__lte=block.end)
        .order_by("-funding_time")
        .first()
    )
    dvol = (
        DeribitVolatilityIndexCandle.objects.filter(open_time__lte=block.end)
        .order_by("-open_time")
        .first()
    )
    freshness = {
        "oi_minutes": _freshness_minutes(block.end, oi.timestamp if oi else None),
        "funding_minutes": _freshness_minutes(
            block.end, funding.funding_time if funding else None
        ),
        "dvol_minutes": _freshness_minutes(
            block.end, dvol.open_time if dvol else None
        ),
    }
    warnings = []
    if freshness["oi_minutes"] is None or freshness["oi_minutes"] > 15:
        warnings.append("OI 数据缺失或距离窗口结束超过 15 分钟。")
    if freshness["funding_minutes"] is None or freshness["funding_minutes"] > 720:
        warnings.append("Funding 数据缺失或距离窗口结束超过 12 小时。")
    if freshness["dvol_minutes"] is None or freshness["dvol_minutes"] > 120:
        warnings.append("DVOL 数据缺失或距离窗口结束超过 2 小时。")

    evidence = _market_evidence(symbol, blocks, 6)
    evidence["data_freshness"] = freshness
    snapshot = {
        "window_start": block.start.isoformat(),
        "window_end": block.end.isoformat(),
        "selection_reason": MarketPilotReport.SelectionReason.SHOCK,
        "market_evidence": evidence,
        "news_available_before_window_end": _news_evidence(block.end),
        "known_data_limitations": [*LIVE_LIMITATIONS, *warnings],
    }
    return snapshot, {
        "ready": True,
        "blocks": block_quality,
        "freshness": freshness,
        "warnings": warnings,
    }


def _future_close(symbol: str, target_at: datetime) -> Decimal | None:
    row = MarketMinute.objects.filter(
        symbol=symbol,
        minute_start=target_at - timedelta(minutes=1),
        kline_closed=True,
    ).first()
    return row.close_price if row is not None else None


def update_due_outcomes(*, now: datetime | None = None) -> int:
    current = now or timezone.now()
    if timezone.is_naive(current):
        raise ValueError("now must be timezone-aware")
    updated = 0
    reports = MarketPilotReport.objects.filter(
        status=MarketPilotReport.Status.AWAITING_OUTCOMES
    ).order_by("window_end")
    for report in reports:
        price = report.input_snapshot.get("market_evidence", {}).get("price", {})
        close_value = price.get("close")
        if close_value is None:
            continue
        base_close = Decimal(str(close_value))
        outcomes = dict(report.future_outcomes)
        changed = False
        for horizon in OUTCOME_HORIZONS:
            key = f"future_{horizon}h_return_pct"
            target_at = report.window_end + timedelta(hours=horizon)
            if key in outcomes or current < target_at:
                continue
            target_close = _future_close(report.symbol, target_at)
            if target_close is None:
                continue
            outcomes[key] = round(float((target_close / base_close - 1) * 100), 6)
            changed = True
        complete = all(
            f"future_{horizon}h_return_pct" in outcomes
            for horizon in OUTCOME_HORIZONS
        )
        if changed or complete:
            report.future_outcomes = outcomes
            if complete:
                report.status = MarketPilotReport.Status.COMPLETED
            report.save(update_fields=["future_outcomes", "status", "updated_at"])
            updated += 1
    return updated


def _report_markdown(report: MarketPilotReport) -> str:
    analysis = report.ai_analysis
    price = report.input_snapshot.get("market_evidence", {}).get("price", {})
    local_start = timezone.localtime(report.window_start).strftime("%m-%d %H:%M")
    local_end = timezone.localtime(report.window_end).strftime("%H:%M")
    direction = "上涨" if float(price.get("return_pct", 0)) >= 0 else "下跌"
    detail_url = ""
    base_url = settings.MARKET_PILOT_PUBLIC_BASE_URL.rstrip("/")
    if base_url:
        detail_url = (
            f"\n> [查看完整证据报告]({base_url}/microstructure/market-pilot/{report.id}/)"
        )
    return (
        f"## ETH 四小时异常{direction}\n"
        f"> 窗口：{local_start}–{local_end}（北京时间）\n"
        f"> 涨跌：**{float(price.get('return_pct', 0)):+.2f}%**\n"
        f"> AI 机制：**{report.get_mechanism_display()}** / "
        f"置信度 {report.get_confidence_display()}\n\n"
        f"**触发背景**：{analysis.get('trigger_assessment', '证据不足')}\n\n"
        f"**放大机制**：{analysis.get('amplifier_assessment', '证据不足')}"
        f"{detail_url}\n\n"
        f"<font color=\"comment\">影子监控报告，不构成交易建议。</font>"
    )


def push_report_notification(
    report: MarketPilotReport, *, client: httpx.Client | None = None
) -> bool:
    webhook_url = settings.MARKET_PILOT_WECHAT_WEBHOOK_URL
    if not webhook_url:
        report.notification_status = MarketPilotReport.NotificationStatus.NOT_CONFIGURED
        report.notification_error = ""
        report.save(
            update_fields=["notification_status", "notification_error", "updated_at"]
        )
        return False

    report.notification_attempts += 1
    owns_client = client is None
    request_client = client or httpx.Client(
        timeout=settings.MARKET_PILOT_WEBHOOK_TIMEOUT_SECONDS,
        trust_env=False,
    )
    try:
        response = request_client.post(
            webhook_url,
            json={
                "msgtype": "markdown",
                "markdown": {"content": _report_markdown(report)},
            },
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("errcode") != 0:
            raise RuntimeError("WeCom webhook returned a non-zero error code")
    except Exception as exc:
        report.notification_status = MarketPilotReport.NotificationStatus.FAILED
        report.notification_error = f"{type(exc).__name__}: push failed"[:300]
        success = False
    else:
        report.notification_status = MarketPilotReport.NotificationStatus.SENT
        report.notification_error = ""
        report.notified_at = timezone.now()
        success = True
    finally:
        if owns_client:
            request_client.close()
    report.save(
        update_fields=[
            "notification_status",
            "notification_attempts",
            "notification_error",
            "notified_at",
            "updated_at",
        ]
    )
    return success


def retry_pending_notifications(*, client: httpx.Client | None = None) -> int:
    sent = 0
    reports = MarketPilotReport.objects.filter(
        notification_status__in=[
            MarketPilotReport.NotificationStatus.PENDING,
            MarketPilotReport.NotificationStatus.FAILED,
        ],
        notification_attempts__lt=MAX_NOTIFICATION_ATTEMPTS,
    ).order_by("created_at")
    for report in reports:
        if push_report_notification(report, client=client):
            sent += 1
    return sent


def _create_live_run(symbol: str, trigger: str) -> MarketPilotRun:
    try:
        with transaction.atomic():
            return MarketPilotRun.objects.create(
                symbol=symbol,
                prompt_version=PROMPT_VERSION,
                configured_model=settings.NEWS_AI_MODEL,
                mode=MarketPilotRun.Mode.LIVE,
                trigger=trigger,
                started_at=timezone.now(),
            )
    except IntegrityError as exc:
        if MarketPilotRun.objects.filter(
            symbol=symbol, status=MarketPilotRun.Status.RUNNING
        ).exists():
            raise MarketMonitorAlreadyRunning(
                f"{symbol} 已有市场监控正在运行。"
            ) from exc
        raise


def monitor_market_windows(
    *,
    symbol: str = "ETHUSDT",
    threshold_pct: Decimal = Decimal("2"),
    trigger: str = MarketPilotRun.Trigger.MANUAL,
    now: datetime | None = None,
    max_windows: int = 6,
    analyzer: Callable = analyze_with_deepseek,
    notification_client: httpx.Client | None = None,
) -> MarketPilotRun:
    current = now or timezone.now()
    if timezone.is_naive(current):
        raise ValueError("now must be timezone-aware")
    if threshold_pct <= 0:
        raise ValueError("threshold_pct must be positive")
    run = _create_live_run(symbol, trigger)
    latest_end = latest_closed_window_end(current)
    starts = [
        latest_end - timedelta(hours=offset * 4)
        for offset in range(max_windows, 0, -1)
    ]
    pending_inputs: list[dict[str, object]] = []
    pending_checks: list[MarketPilotWindowCheck] = []
    attempted = 0
    try:
        update_due_outcomes(now=current)
        for window_start in starts:
            check, created = MarketPilotWindowCheck.objects.get_or_create(
                symbol=symbol,
                window_start=window_start,
                defaults={
                    "run": run,
                    "window_end": window_start + timedelta(hours=4),
                    "threshold_pct": threshold_pct,
                },
            )
            if not created and check.status in {
                MarketPilotWindowCheck.Status.NORMAL,
                MarketPilotWindowCheck.Status.ANALYZED,
            }:
                continue
            attempted += 1
            check.run = run
            check.threshold_pct = threshold_pct
            check.attempt_count += 1
            snapshot, quality = build_live_input(symbol, window_start)
            check.data_quality = quality
            if snapshot is None:
                check.status = MarketPilotWindowCheck.Status.WAITING_DATA
                check.safe_error_summary = str(quality.get("reason", "等待数据"))[:500]
                check.save()
                continue
            return_pct = Decimal(
                str(snapshot["market_evidence"]["price"]["return_pct"])
            )
            check.return_pct = return_pct
            check.safe_error_summary = ""
            if abs(return_pct) < threshold_pct:
                check.status = MarketPilotWindowCheck.Status.NORMAL
                check.save()
                continue
            check.status = MarketPilotWindowCheck.Status.ANALYZING
            check.save()
            pending_inputs.append(snapshot)
            pending_checks.append(check)

        ai_metadata = {
            "actual_models": [],
            "request_count": 0,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
        if pending_inputs:
            analyses, ai_metadata = analyzer(pending_inputs)
            analyses_by_start = {item["window_start"]: item for item in analyses}
            notification_status = (
                MarketPilotReport.NotificationStatus.PENDING
                if settings.MARKET_PILOT_WECHAT_WEBHOOK_URL
                else MarketPilotReport.NotificationStatus.NOT_CONFIGURED
            )
            for check, snapshot in zip(pending_checks, pending_inputs):
                analysis = analyses_by_start[str(snapshot["window_start"])]
                report, _ = MarketPilotReport.objects.update_or_create(
                    symbol=symbol,
                    window_start=check.window_start,
                    defaults={
                        "run": run,
                        "window_end": check.window_end,
                        "selection_reason": MarketPilotReport.SelectionReason.SHOCK,
                        "mechanism": str(analysis["mechanism"]),
                        "confidence": str(analysis["confidence"]),
                        "input_snapshot": snapshot,
                        "ai_analysis": analysis,
                        "future_outcomes": {},
                        "status": MarketPilotReport.Status.AWAITING_OUTCOMES,
                        "notification_status": notification_status,
                    },
                )
                check.report = report
                check.status = MarketPilotWindowCheck.Status.ANALYZED
                check.save(update_fields=["report", "status", "updated_at"])

        usage = ai_metadata["usage"]
        run.status = MarketPilotRun.Status.SUCCESS
        run.actual_models = ai_metadata["actual_models"]
        run.window_count = attempted
        run.request_count = int(ai_metadata["request_count"])
        run.input_tokens = int(usage["prompt_tokens"])
        run.output_tokens = int(usage["completion_tokens"])
        run.total_tokens = int(usage["total_tokens"])
        run.finished_at = timezone.now()
        run.save()
        update_due_outcomes(now=current)
        retry_pending_notifications(client=notification_client)
    except Exception as exc:
        MarketPilotWindowCheck.objects.filter(
            pk__in=[check.pk for check in pending_checks],
            status=MarketPilotWindowCheck.Status.ANALYZING,
        ).update(
            status=MarketPilotWindowCheck.Status.FAILED,
            safe_error_summary=f"{type(exc).__name__}: analysis failed",
        )
        run.status = MarketPilotRun.Status.FAILED
        run.safe_error_summary = f"{type(exc).__name__}: market monitor failed"[:500]
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "safe_error_summary", "finished_at"])
        raise
    return run
