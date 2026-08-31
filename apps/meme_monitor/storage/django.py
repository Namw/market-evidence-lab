from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from django.db.models import F

from apps.meme_monitor.domain import MemeAnomalyEvent, TokenMarketSnapshot
from apps.meme_monitor.models import (
    MemeAnomalyEventRecord,
    MemeMarketSnapshot,
    MemeMonitorCycle,
    MemeMonitorRun,
    MemePairState,
)


class DjangoMemeMonitorStorage:
    def __init__(self, *, source: str = "geckoterminal") -> None:
        self.source = source

    def start_run(
        self,
        *,
        chain: str,
        mode: str,
        process_id: int,
        started_at: datetime,
    ) -> MemeMonitorRun:
        MemeMonitorRun.objects.filter(
            source=self.source,
            chain=chain,
            status=MemeMonitorRun.Status.RUNNING,
        ).update(
            status=MemeMonitorRun.Status.STOPPED,
            stopped_at=started_at,
            latest_error="新的监听进程已接管；此前运行未正常结束。",
        )
        return MemeMonitorRun.objects.create(
            source=self.source[:40],
            chain=chain[:32],
            mode=mode,
            status=MemeMonitorRun.Status.RUNNING,
            process_id=process_id,
            started_at=started_at,
            heartbeat_at=started_at,
        )

    def finish_run(
        self,
        run_id: int,
        *,
        status: str,
        stopped_at: datetime,
        error_message: str = "",
    ) -> None:
        MemeMonitorRun.objects.filter(pk=run_id).update(
            status=status,
            stopped_at=stopped_at,
            heartbeat_at=stopped_at,
            latest_error=error_message[:500],
        )

    def start_cycle(self, run_id: int, *, started_at: datetime) -> MemeMonitorCycle:
        MemeMonitorRun.objects.filter(pk=run_id).update(heartbeat_at=started_at)
        return MemeMonitorCycle.objects.create(
            run_id=run_id,
            status=MemeMonitorCycle.Status.RUNNING,
            started_at=started_at,
        )

    def finish_cycle(
        self,
        run_id: int,
        cycle_id: int,
        *,
        finished_at: datetime,
        fetched_pairs: int,
        tracked_pairs: int,
        saved_snapshots: int,
        detected_anomalies: int,
        warning_message: str = "",
    ) -> None:
        cycle_status = (
            MemeMonitorCycle.Status.PARTIAL
            if warning_message
            else MemeMonitorCycle.Status.SUCCEEDED
        )
        MemeMonitorCycle.objects.filter(pk=cycle_id).update(
            status=cycle_status,
            finished_at=finished_at,
            fetched_pairs=fetched_pairs,
            tracked_pairs=tracked_pairs,
            saved_snapshots=saved_snapshots,
            detected_anomalies=detected_anomalies,
            error_message=warning_message[:500],
        )
        MemeMonitorRun.objects.filter(pk=run_id).update(
            heartbeat_at=finished_at,
            cycle_count=F("cycle_count") + 1,
            successful_cycle_count=F("successful_cycle_count") + 1,
            latest_error=warning_message[:500],
        )

    def fail_cycle(
        self,
        run_id: int,
        cycle_id: int,
        *,
        finished_at: datetime,
        error_message: str,
    ) -> None:
        safe_error = error_message[:500]
        MemeMonitorCycle.objects.filter(pk=cycle_id).update(
            status=MemeMonitorCycle.Status.FAILED,
            finished_at=finished_at,
            error_message=safe_error,
        )
        MemeMonitorRun.objects.filter(pk=run_id).update(
            heartbeat_at=finished_at,
            cycle_count=F("cycle_count") + 1,
            failed_cycle_count=F("failed_cycle_count") + 1,
            latest_error=safe_error,
        )

    def recent_pairs(
        self,
        *,
        chain: str,
        created_since: datetime,
        limit: int,
    ) -> list[TokenMarketSnapshot]:
        records = list(
            MemePairState.objects.filter(
                source=self.source,
                chain=chain,
                pair_created_at__gte=created_since,
            )
            .order_by("-observed_at")
        )
        records.sort(
            key=lambda record: record.pair_created_at,
            reverse=True,
        )
        return [_snapshot_from_state(record) for record in records[:limit]]

    def volume_histories(
        self,
        *,
        chain: str,
        pair_addresses: Sequence[str],
        limit: int,
    ) -> dict[str, list[Decimal]]:
        rows = MemePairState.objects.filter(
            source=self.source,
            chain=chain,
            pair_address__in=pair_addresses,
        ).values_list("pair_address", "volume_5m_history")
        return {
            address: [_decimal(value) for value in reversed(history[-limit:])]
            for address, history in rows
        }

    def upsert_pair_states(
        self,
        snapshots: Sequence[TokenMarketSnapshot],
        *,
        volume_history_limit: int,
    ) -> int:
        existing = {
            state.pair_address: state
            for state in MemePairState.objects.filter(
                source=self.source,
                chain__in={item.chain for item in snapshots},
                pair_address__in={item.pair_address for item in snapshots},
            )
        }
        states: list[MemePairState] = []
        for item in snapshots:
            prior_state = existing.get(item.pair_address)
            prior_history = prior_state.volume_5m_history if prior_state else []
            history = list(prior_history)
            if item.volume_5m is not None:
                history.append(str(item.volume_5m))
            states.append(MemePairState(
                source=self.source[:40],
                chain=item.chain[:32],
                dex=item.dex[:80],
                token_address=item.token_address[:128],
                pair_address=item.pair_address[:128],
                symbol=item.symbol[:80],
                name=item.name[:300],
                pair_created_at=item.pair_created_at,
                price_usd=_bounded(item.price_usd, integer_digits=26),
                liquidity_usd=_bounded(item.liquidity_usd, integer_digits=42),
                market_cap=_bounded(item.market_cap, integer_digits=42),
                fdv=_bounded(item.fdv, integer_digits=42),
                volume_5m=_bounded(item.volume_5m, integer_digits=42),
                volume_1h=_bounded(item.volume_1h, integer_digits=42),
                buys_5m=_positive_integer(item.buys_5m),
                sells_5m=_positive_integer(item.sells_5m),
                price_change_5m=_bounded(item.price_change_5m, integer_digits=22),
                price_change_1h=_bounded(item.price_change_1h, integer_digits=22),
                volume_5m_history=history[-volume_history_limit:],
                observed_at=item.timestamp,
            ))
        if not states:
            return 0
        MemePairState.objects.bulk_create(
            states,
            update_conflicts=True,
            unique_fields=["source", "chain", "pair_address"],
            update_fields=[
                "dex", "token_address", "symbol", "name", "pair_created_at",
                "price_usd", "liquidity_usd", "market_cap", "fdv", "volume_5m",
                "volume_1h", "buys_5m", "sells_5m", "price_change_5m",
                "price_change_1h", "volume_5m_history", "observed_at", "updated_at",
            ],
        )
        return len(states)

    def in_cooldown(
        self,
        *,
        chain: str,
        token_address: str,
        anomaly_type: str,
        since: datetime,
    ) -> bool:
        return MemeAnomalyEventRecord.objects.filter(
            chain=chain,
            token_address=token_address,
            anomaly_type=anomaly_type,
            event_time__gte=since,
        ).exists()

    def save_event(
        self,
        event: MemeAnomalyEvent,
    ) -> MemeAnomalyEventRecord:
        return MemeAnomalyEventRecord.objects.create(
            event_id=event.event_id,
            source=self.source[:40],
            anomaly_type=event.anomaly_type,
            event_time=event.event_time,
            chain=event.chain[:32],
            token_address=event.token_address[:128],
            pair_address=event.pair_address[:128],
            symbol=event.symbol[:80],
            name=event.name[:300],
            pair_age_minutes=event.pair_age_minutes,
            price_usd=_bounded(event.price_usd, integer_digits=26),
            price_change_5m=_bounded(event.price_change_5m, integer_digits=22),
            price_change_1h=_bounded(event.price_change_1h, integer_digits=22),
            volume_5m=_bounded(event.volume_5m, integer_digits=42),
            liquidity_usd=_bounded(event.liquidity_usd, integer_digits=42),
            buys_5m=_positive_integer(event.buys_5m),
            sells_5m=_positive_integer(event.sells_5m),
            triggered_rules=list(event.triggered_rules),
        )


def _snapshot_from_state(record: MemePairState) -> TokenMarketSnapshot:
    return TokenMarketSnapshot(
        chain=record.chain,
        dex=record.dex,
        token_address=record.token_address,
        pair_address=record.pair_address,
        symbol=record.symbol,
        name=record.name,
        pair_created_at=record.pair_created_at,
        price_usd=record.price_usd,
        liquidity_usd=record.liquidity_usd,
        market_cap=record.market_cap,
        fdv=record.fdv,
        volume_5m=record.volume_5m,
        volume_1h=record.volume_1h,
        buys_5m=record.buys_5m,
        sells_5m=record.sells_5m,
        price_change_5m=record.price_change_5m,
        price_change_1h=record.price_change_1h,
        timestamp=record.observed_at,
    )


def _decimal(value: str | int | float | Decimal) -> Decimal:
    return Decimal(str(value))


def _bounded(value: Decimal | None, *, integer_digits: int) -> Decimal | None:
    if value is None or not value.is_finite():
        return None
    if abs(value) >= Decimal(10) ** integer_digits:
        return None
    return value


def _positive_integer(value: int | None) -> int | None:
    if value is None or not 0 <= value <= 2_147_483_647:
        return None
    return value
