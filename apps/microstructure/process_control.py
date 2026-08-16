from __future__ import annotations

import os
import signal
import subprocess
import sys
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import MicrostructureCollectorRun

ACTIVE_STATUSES = {
    MicrostructureCollectorRun.Status.STARTING,
    MicrostructureCollectorRun.Status.RUNNING,
    MicrostructureCollectorRun.Status.STOPPING,
}


class CollectorControlError(RuntimeError):
    pass


def _process_command(process_id: int) -> str:
    if process_id <= 0:
        return ""
    result = subprocess.run(
        ["ps", "-p", str(process_id), "-o", "command="],
        check=False,
        capture_output=True,
        text=True,
        timeout=2,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def process_matches_run(run: MicrostructureCollectorRun) -> bool:
    if not run.process_id:
        return False
    command = _process_command(run.process_id)
    return (
        "manage.py collect_orderbook" in command
        and f"--run-id {run.pk}" in command
    )


def _unmanaged_collector_exists() -> bool:
    result = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        check=False,
        capture_output=True,
        text=True,
        timeout=2,
    )
    if result.returncode != 0:
        return False
    for line in result.stdout.splitlines():
        if "manage.py collect_orderbook" in line:
            return True
    return False


@transaction.atomic
def _reserve_run(symbol: str) -> MicrostructureCollectorRun:
    now = timezone.now()
    active_runs = list(
        MicrostructureCollectorRun.objects.select_for_update()
        .filter(status__in=ACTIVE_STATUSES)
        .order_by("-created_at")
    )
    for active in active_runs:
        recently_starting = (
            active.status == MicrostructureCollectorRun.Status.STARTING
            and active.updated_at >= now - timedelta(seconds=15)
        )
        if recently_starting or process_matches_run(active):
            raise CollectorControlError("盘口采集已经在运行或正在启动。")
        active.status = MicrostructureCollectorRun.Status.FAILED
        active.connection_state = MicrostructureCollectorRun.ConnectionState.DISCONNECTED
        active.stopped_at = now
        active.error_message = "采集进程已不存在，运行状态已自动结束。"
        active.save(
            update_fields=[
                "status",
                "connection_state",
                "stopped_at",
                "error_message",
                "updated_at",
            ]
        )
    return MicrostructureCollectorRun.objects.create(symbol=symbol)


def launch_collector(*, symbol: str) -> MicrostructureCollectorRun:
    if _unmanaged_collector_exists():
        raise CollectorControlError(
            "检测到终端启动的盘口采集，请先在终端停止后再从页面启动。"
        )
    run = _reserve_run(symbol)
    command = [
        sys.executable,
        str(settings.BASE_DIR / "manage.py"),
        "collect_orderbook",
        "--symbol",
        symbol,
        "--run-id",
        str(run.pk),
    ]
    try:
        process = subprocess.Popen(
            command,
            cwd=settings.BASE_DIR,
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
    except OSError as exc:
        run.status = MicrostructureCollectorRun.Status.FAILED
        run.connection_state = MicrostructureCollectorRun.ConnectionState.DISCONNECTED
        run.stopped_at = timezone.now()
        run.error_message = f"{exc.__class__.__name__}: 无法启动采集进程"
        run.save(
            update_fields=[
                "status",
                "connection_state",
                "stopped_at",
                "error_message",
                "updated_at",
            ]
        )
        raise CollectorControlError("无法启动盘口采集进程。") from exc
    run.process_id = process.pid
    run.save(update_fields=["process_id", "updated_at"])
    return run


def stop_collector() -> MicrostructureCollectorRun:
    error_message = ""
    with transaction.atomic():
        run = (
            MicrostructureCollectorRun.objects.select_for_update()
            .filter(status__in=ACTIVE_STATUSES)
            .order_by("-created_at")
            .first()
        )
        if run is None:
            error_message = "当前没有正在运行的页面采集任务。"
        elif not process_matches_run(run):
            run.status = MicrostructureCollectorRun.Status.FAILED
            run.connection_state = (
                MicrostructureCollectorRun.ConnectionState.DISCONNECTED
            )
            run.stopped_at = timezone.now()
            run.error_message = "未找到对应采集进程，未发送停止信号。"
            run.save(
                update_fields=[
                    "status",
                    "connection_state",
                    "stopped_at",
                    "error_message",
                    "updated_at",
                ]
            )
            error_message = "未找到对应采集进程，状态已更新。"
        else:
            run.status = MicrostructureCollectorRun.Status.STOPPING
            run.save(update_fields=["status", "updated_at"])
    if error_message:
        raise CollectorControlError(error_message)
    try:
        os.kill(run.process_id, signal.SIGTERM)
    except OSError as exc:
        MicrostructureCollectorRun.objects.filter(pk=run.pk).update(
            status=MicrostructureCollectorRun.Status.FAILED,
            connection_state=MicrostructureCollectorRun.ConnectionState.DISCONNECTED,
            stopped_at=timezone.now(),
            error_message=f"{exc.__class__.__name__}: 无法停止采集进程",
        )
        raise CollectorControlError("无法停止盘口采集进程。") from exc
    return run
