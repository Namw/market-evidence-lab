"""Launch the local worker without tying its lifetime to the web process."""
import fcntl
import subprocess
import sys
import time

import psycopg
from django.conf import settings
from django.db import connections
from psycopg.rows import dict_row

from .services import worker_online

WORKER_LOCK = 737214019
RECOVERY_LOCK = 737214020
WORKER_APPLICATION = "market-evidence-lab.trading-assistant"
RECOVERY_GRACE_SECONDS = 360
STARTUP_WAIT_SECONDS = 5


class WorkerStartError(RuntimeError):
    """An actionable message safe to display without exposing raw exceptions."""


def worker_connection_params():
    params = connections["default"].get_connection_params()
    params.pop("cursor_factory", None)
    params.update(autocommit=True, prepare_threshold=0, row_factory=dict_row,
                  connect_timeout=5, keepalives=1, keepalives_idle=30,
                  keepalives_interval=10, keepalives_count=3)
    # Django's supplied timestamp adapters expect UTC; direct psycopg
    # connections do not run Django's session timezone initialization.
    params["options"] = params.get("options", "") + " -c timezone=UTC"
    return params


HOLDER_SQL = """
    SELECT a.pid, a.backend_start, a.state_change,
           (a.state = 'idle'
            AND a.backend_start < statement_timestamp() - %s * interval '1 second'
            AND a.state_change < statement_timestamp() - %s * interval '1 second'
            AND (a.application_name = %s OR
                 (a.application_name = '' AND a.query = 'SELECT v FROM checkpoint_migrations ORDER BY v DESC LIMIT 1'))
            AND NOT EXISTS (SELECT 1 FROM public.trading_assistant_workerheartbeat
                            WHERE name = 'default'
                              AND seen_at >= statement_timestamp() - %s * interval '1 second')
           ) AS recoverable
    FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid
    WHERE l.locktype = 'advisory' AND l.classid = 0 AND l.objid = %s
      AND l.objsubid = 1 AND l.granted
      AND l.database = (SELECT oid FROM pg_database WHERE datname = current_database())
      AND a.datname = current_database() AND a.usename = current_user
"""


def repair_stale_worker_lock():
    """Only terminate a precisely identified, long-idle assistant session.

    The grace period exceeds a complete analysis timeout. Recheck the lock,
    backend identity, activity and heartbeat in the terminating SQL statement.
    No user-supplied PID or SQL is accepted.
    """
    grace = max(RECOVERY_GRACE_SECONDS, settings.TRADING_ASSISTANT_MAX_RUN_SECONDS + 60)
    args = (grace, grace, WORKER_APPLICATION, grace, WORKER_LOCK)
    try:
        params = worker_connection_params()
        params["options"] = params.get("options", "") + " -c statement_timeout=5000"
        with psycopg.connect(**params) as conn:
            if not conn.execute("SELECT pg_try_advisory_lock(%s) AS locked", (RECOVERY_LOCK,)).fetchone()["locked"]:
                raise WorkerStartError("另一个启动请求正在检查服务，请稍后重试。")
            holder = conn.execute(HOLDER_SQL, args).fetchone()
            if not holder:
                return False
            if not holder["recoverable"]:
                raise WorkerStartError("已有分析进程占用服务，暂不符合自动清理条件。系统会保护正在启动或处理任务的进程，请稍后重试。")
            # backend_start prevents PID reuse; state_change prevents killing a
            # connection that became active after the first observation.
            row = conn.execute(
                "SELECT pg_terminate_backend(pid) AS terminated FROM (" + HOLDER_SQL + ") holder "
                "WHERE recoverable AND pid = %s AND backend_start = %s AND state_change = %s",
                (*args, holder["pid"], holder["backend_start"], holder["state_change"]),
            ).fetchone()
            if not row or not row["terminated"]:
                raise WorkerStartError("旧连接状态已变化，未进行清理。请稍后重试。")
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                remaining = conn.execute(HOLDER_SQL, args).fetchone()
                if not remaining:
                    return True
                if remaining["pid"] != holder["pid"] or remaining["backend_start"] != holder["backend_start"]:
                    raise WorkerStartError("已有其他分析进程接管，请稍后查看在线状态。")
                time.sleep(0.1)
            raise WorkerStartError("已通知旧连接退出，正在等待释放，请稍后重试。")
    except psycopg.errors.InsufficientPrivilege:
        raise WorkerStartError("数据库账号没有清理旧分析连接的权限，请联系管理员处理该连接。") from None
    except psycopg.Error:
        raise WorkerStartError("无法完成数据库检查，请确认数据库隧道可用且迁移已完成，再点击重试。") from None


def startup_failure(log, start_offset):
    log.seek(0, 2)
    end = log.tell()
    log.seek(max(start_offset, end - 4000))
    tail = log.read()
    if "已有开仓分析助手后台进程运行" in tail:
        return "已有其他分析进程获得运行锁，请稍后重试；符合条件的失效连接会自动清理。"
    if "does not exist" in tail or "unapplied migration" in tail:
        return "分析服务需要的数据库结构尚未就绪，请先执行数据库迁移。"
    if "OperationalError" in tail or "connection failed" in tail:
        return "分析服务无法连接数据库，请恢复数据库隧道后重试。"
    return "分析进程启动后退出，请查看 .local_trading_assistant.log 中的最新错误。"

def start_worker():
    if worker_online():
        return {"worker_online": True, "starting": False}
    # The child inherits this lock for its lifetime. Closing the parent's copy
    # must not explicitly unlock it; concurrent web requests cannot spawn twice.
    with (settings.BASE_DIR / ".local_trading_assistant_launch.log").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"worker_online": False, "starting": True}
        recovered = repair_stale_worker_lock()
        with (settings.BASE_DIR / ".local_trading_assistant.log").open("a+", encoding="utf-8", errors="replace") as log:
            log.seek(0, 2)
            start_offset = log.tell()
            process = subprocess.Popen(
                [sys.executable, "-u", str(settings.BASE_DIR / "manage.py"), "run_trading_assistant"],
                cwd=settings.BASE_DIR, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                pass_fds=(lock.fileno(),),
            )
            deadline = time.monotonic() + STARTUP_WAIT_SECONDS
            while time.monotonic() < deadline:
                if worker_online():
                    return {"worker_online": True, "starting": False, "recovered": recovered}
                if process.poll() is not None:
                    raise WorkerStartError(startup_failure(log, start_offset))
                time.sleep(0.1)
        return {"worker_online": False, "starting": True, "recovered": recovered}
