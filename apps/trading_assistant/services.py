from datetime import timedelta

from django.db import transaction
from django.db.models.functions import Now
from django.utils import timezone

from .models import AnalysisTurn, Conversation, WorkerHeartbeat


def worker_online():
    return WorkerHeartbeat.objects.filter(name="default", seen_at__gte=Now() - timedelta(seconds=30), seen_at__lte=Now() + timedelta(seconds=5)).exists()


@transaction.atomic
def submit_turn(conversation_id, *, question, request_id, refresh_data, horizon_minutes):
    conversation = Conversation.objects.select_for_update().get(pk=conversation_id)
    existing = conversation.turns.filter(request_id=request_id).first()
    if existing:
        return existing
    if conversation.turns.filter(status__in=[AnalysisTurn.Status.QUEUED, AnalysisTurn.Status.RUNNING]).exists():
        raise ValueError("这个会话还有问题正在分析，请等本轮完成后再追问。")
    turn = AnalysisTurn.objects.create(
        conversation=conversation, question=question, request_id=request_id,
        refresh_data=refresh_data, horizon_minutes=horizon_minutes,
    )
    if conversation.title == "新的开仓分析":
        conversation.title = question[:80]
    conversation.horizon_minutes = horizon_minutes
    conversation.save(update_fields=["title", "horizon_minutes", "updated_at"])
    return turn


def mark_success(turn, report):
    AnalysisTurn.objects.filter(pk=turn.pk).update(
        report=report, status=AnalysisTurn.Status.SUCCEEDED,
        progress="分析完成", safe_error="", finished_at=timezone.now(),
    )
    Conversation.objects.filter(pk=turn.conversation_id).update(
        updated_at=timezone.now(), horizon_minutes=report.get("horizon_minutes", turn.horizon_minutes),
    )


def mark_failed(turn, message):
    AnalysisTurn.objects.filter(pk=turn.pk).update(
        status=AnalysisTurn.Status.FAILED, progress="本轮未完成", safe_error=message[:300],
        finished_at=timezone.now(),
    )
