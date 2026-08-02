import json
from datetime import UTC, datetime, timedelta

from django.utils import timezone

from apps.collection.models import CollectionRun
from apps.news_data.models import NewsRawRecord, NewsSource


NOW = datetime(2026, 8, 1, 8, tzinfo=UTC)


def make_record(*, source_code="binance_announcements", title="A material update", summary="Summary", category="Latest"):
    source = NewsSource.objects.get(code=source_code)
    run = CollectionRun.objects.create(
        data_type=CollectionRun.DataType.NEWS,
        news_source=source,
        range_start=NOW - timedelta(days=1),
        range_end=NOW,
        trigger=CollectionRun.Trigger.MANUAL,
        status=CollectionRun.Status.SUCCESS,
        started_at=NOW,
        finished_at=NOW,
    )
    suffix = f"{NewsRawRecord.objects.count() + 1}-{source_code}"
    return NewsRawRecord.objects.create(
        source=source,
        source_item_id=suffix,
        original_url=f"https://example.com/{suffix}",
        canonical_url=f"https://example.com/{suffix}",
        title=title,
        summary=summary,
        published_at=NOW,
        language="en",
        source_category=category,
        source_tags=[category],
        first_seen_at=NOW,
        last_seen_at=NOW,
        identity_hash=(suffix.encode().hex() + "0" * 64)[:64],
        content_hash=(suffix.encode().hex() + "1" * 64)[:64],
        raw_payload={"must_not_leave_database": True},
        first_collection_run=run,
        last_collection_run=run,
    )


def ai_item(news_id, **overrides):
    item = {
        "news_id": news_id,
        "conclusion": "bullish",
        "rationale": "事件明确增加 ETH 的采用或需求。",
        "content_summary": "",
    }
    item.update(overrides)
    return item


def completion_payload(items, *, finish_reason="stop", model="deepseek-v4-flash", usage=None):
    return {
        "model": model,
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {"content": json.dumps({"items": items}, ensure_ascii=False)},
            }
        ],
        "usage": usage or {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
        },
    }
