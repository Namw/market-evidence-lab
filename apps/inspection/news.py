from __future__ import annotations

from django.db.models import Sum
from django.utils import timezone

from apps.collection.models import CollectionRun
from apps.news_data.models import NewsCollectionDiagnostic, NewsFeed, NewsSource

from .models import NewsInspectionRun, empty_news_dimensions


CRITICAL_STOP_REASONS = {
    NewsCollectionDiagnostic.StopReason.SAFETY_PAGE_LIMIT,
    NewsCollectionDiagnostic.StopReason.PAGINATION_LOOP,
    NewsCollectionDiagnostic.StopReason.REQUEST_FAILED,
}


def inspect_news_collection(
    collection_run: CollectionRun,
) -> NewsInspectionRun:
    if collection_run.data_type != CollectionRun.DataType.NEWS:
        raise ValueError("A news CollectionRun is required.")
    if collection_run.news_source_id is None:
        raise ValueError("News CollectionRun has no source.")
    source = collection_run.news_source
    feed = collection_run.news_feed
    inspection = NewsInspectionRun.objects.create(
        source=source,
        feed=feed,
        range_start=collection_run.range_start,
        range_end=collection_run.range_end,
        trigger=collection_run.trigger,
        source_collection_run=collection_run,
        started_at=timezone.now(),
    )
    try:
        diagnostics = list(
            collection_run.news_diagnostics.order_by("request_started_at", "id")
        )
        totals = collection_run.news_diagnostics.aggregate(
            candidate=Sum("candidate_count"),
            parsed=Sum("parsed_count"),
            eligible=Sum("eligible_count"),
            inserted=Sum("inserted_count"),
            updated=Sum("updated_count"),
            duplicate=Sum("duplicate_count"),
            invalid=Sum("invalid_count"),
        )
        critical = any(d.stop_reason in CRITICAL_STOP_REASONS for d in diagnostics)
        availability = bool(diagnostics) and all(
            d.http_status is not None and 200 <= d.http_status < 300
            for d in diagnostics
        )
        parsed_count = totals["parsed"] or 0
        parsing = parsed_count > 0 and not any(
            d.error_code
            in {
                "invalid_xml",
                "unknown_feed",
                "zero_parsed_items",
                "zero_first_page",
                "invalid_json",
                "schema_changed",
            }
            for d in diagnostics
        )
        coverage = (
            collection_run.status == CollectionRun.Status.SUCCESS
            and bool(diagnostics)
            and diagnostics[-1].coverage_complete
            and not critical
        )
        invalid_count = totals["invalid"] or 0
        key_fields = invalid_count == 0
        timeliness = collection_run.finished_at is not None
        dimensions = {
            "availability": availability,
            "parsing": parsing,
            "coverage": coverage,
            "key_fields": key_fields,
            "timeliness": timeliness,
        }
        reasons: list[str] = []
        if not availability:
            reasons.append("来源请求不可用或未留下成功响应诊断。")
        if not parsing:
            reasons.append("来源内容未能解析出可信条目。")
        if not coverage:
            reasons.append("本次运行无法排除漏采风险，可信覆盖不完整。")
        if invalid_count:
            reasons.append(f"有 {invalid_count} 条候选记录缺少关键字段或格式无效。")
        retry_count = sum(d.retry_count for d in diagnostics)
        detail_fetch_failure_count = sum(
            int((d.details or {}).get("detail_fetch_failure_count") or 0)
            for d in diagnostics
        )
        if retry_count:
            reasons.append(f"来源请求发生 {retry_count} 次有限重试。")
        if any(
            d.stop_reason == NewsCollectionDiagnostic.StopReason.SOURCE_HISTORY_LIMITED
            for d in diagnostics
        ):
            reasons.append("来源当前可见历史不足读取窗口，已完整读取其可见范围。")
        for diagnostic in diagnostics:
            if diagnostic.error_summary and diagnostic.error_summary not in reasons:
                reasons.append(diagnostic.error_summary)

        if not all((availability, parsing, coverage, timeliness)):
            quality_status = NewsInspectionRun.QualityStatus.FAILED
        elif retry_count or invalid_count or detail_fetch_failure_count or any(
            d.stop_reason == NewsCollectionDiagnostic.StopReason.SOURCE_HISTORY_LIMITED
            for d in diagnostics
        ) or any((d.details or {}).get("xml_recovered") for d in diagnostics):
            quality_status = NewsInspectionRun.QualityStatus.WARNING
        else:
            quality_status = NewsInspectionRun.QualityStatus.PASSED

        inspection.status = NewsInspectionRun.Status.SUCCESS
        inspection.quality_status = quality_status
        inspection.coverage_complete = coverage
        inspection.candidate_count = totals["candidate"] or 0
        inspection.parsed_count = parsed_count
        inspection.eligible_count = totals["eligible"] or 0
        inspection.inserted_count = totals["inserted"] or 0
        inspection.updated_count = totals["updated"] or 0
        inspection.duplicate_count = totals["duplicate"] or 0
        inspection.invalid_count = invalid_count
        inspection.dimensions = dimensions
        inspection.reasons = reasons
    except Exception as exc:
        inspection.status = NewsInspectionRun.Status.FAILED
        inspection.quality_status = NewsInspectionRun.QualityStatus.FAILED
        inspection.coverage_complete = False
        inspection.dimensions = empty_news_dimensions()
        inspection.reasons = ["新闻质量检查执行失败。"]
        inspection.error_message = f"{exc.__class__.__name__}: inspection failed"
    finally:
        inspection.finished_at = timezone.now()
        inspection.save()

    if feed is not None:
        feed.last_run_at = collection_run.finished_at or collection_run.started_at
        feed.last_inspection_status = inspection.quality_status
        if inspection.quality_status in {
            NewsInspectionRun.QualityStatus.PASSED,
            NewsInspectionRun.QualityStatus.WARNING,
        } and inspection.coverage_complete:
            feed.trusted_coverage_end = collection_run.range_end
        feed.health_status = feed.health_at(inspection.finished_at)
        feed.save(
            update_fields=[
                "last_run_at",
                "last_inspection_status",
                "trusted_coverage_end",
                "health_status",
                "updated_at",
            ]
        )

    enabled_feeds = list(source.feeds.filter(enabled=True))
    source.last_run_at = max(
        (item.last_run_at for item in enabled_feeds if item.last_run_at),
        default=collection_run.finished_at or collection_run.started_at,
    )
    statuses = {item.last_inspection_status for item in enabled_feeds}
    if NewsSource.InspectionStatus.FAILED in statuses:
        source.last_inspection_status = NewsSource.InspectionStatus.FAILED
    elif statuses <= {NewsSource.InspectionStatus.PASSED}:
        source.last_inspection_status = NewsSource.InspectionStatus.PASSED
    elif statuses & {
        NewsSource.InspectionStatus.PASSED,
        NewsSource.InspectionStatus.WARNING,
    }:
        source.last_inspection_status = NewsSource.InspectionStatus.WARNING
    else:
        source.last_inspection_status = NewsSource.InspectionStatus.NEVER_RUN
    coverage_values = [item.trusted_coverage_end for item in enabled_feeds]
    if coverage_values and all(coverage_values):
        source.trusted_coverage_end = min(coverage_values)
    source.health_status = source.health_at(inspection.finished_at)
    source.save(
        update_fields=[
            "last_run_at",
            "last_inspection_status",
            "trusted_coverage_end",
            "health_status",
            "updated_at",
        ]
    )
    return inspection
