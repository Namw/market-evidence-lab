"""One bounded worker with persistent LangGraph state and a database-wide lock."""
import signal
import threading

import httpx
import psycopg
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, close_old_connections, connections
from django.db.models.functions import Now
from django.utils import timezone
from langgraph.checkpoint.postgres import PostgresSaver

from apps.trading_assistant.agent import run_agent
from apps.trading_assistant.report_recovery import ReportGenerationError
from apps.trading_assistant.models import AnalysisTurn, WorkerHeartbeat
from apps.trading_assistant.services import mark_failed, mark_success
from apps.trading_assistant.schemas import checkpoint_serializer
from apps.trading_assistant.worker_control import WORKER_LOCK, WORKER_APPLICATION, worker_connection_params


def heartbeat(stop):
    while not stop.is_set():
        try:
            close_old_connections()
            WorkerHeartbeat.objects.update_or_create(name="default", defaults={"seen_at": Now()})
        except Exception:
            pass  # Database failures surface in the main worker; never log credentials.
        finally:
            connections.close_all()
        stop.wait(5)


class Command(BaseCommand):
    help = "运行开仓分析助手后台进程；--once 只处理一轮。"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        db = settings.DATABASES["default"]
        if db["ENGINE"] != "django.db.backends.postgresql":
            raise CommandError("后台进程需要 PostgreSQL 持久化。")
        params = worker_connection_params()
        params["application_name"] = WORKER_APPLICATION
        stop = threading.Event()
        old_handlers = {}

        def interrupt(signum, frame):
            raise KeyboardInterrupt

        def timeout(signum, frame):
            raise TimeoutError("分析超时")

        current = None
        pulse = None
        try:
            with psycopg.connect(**params) as conn:
                if not conn.execute("SELECT pg_try_advisory_lock(%s) AS acquired", (WORKER_LOCK,)).fetchone()["acquired"]:
                    raise CommandError("已有开仓分析助手后台进程运行。")
                # Keep framework tables separate from Django application tables.
                conn.execute("CREATE SCHEMA IF NOT EXISTS trading_assistant_agent")
                conn.execute("SET search_path TO trading_assistant_agent")
                saver = PostgresSaver(conn, serde=checkpoint_serializer())
                saver.setup()
                AnalysisTurn.objects.filter(status=AnalysisTurn.Status.RUNNING).update(
                    status=AnalysisTurn.Status.QUEUED, progress="后台恢复，等待继续分析",
                )
                for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGALRM):
                    old_handlers[sig] = signal.getsignal(sig)
                    signal.signal(sig, timeout if sig == signal.SIGALRM else interrupt)
                pulse = threading.Thread(target=heartbeat, args=(stop,), daemon=True)
                pulse.start()
                self.stdout.write("开仓分析助手已就绪。")
                while True:
                    # Detect loss of the lock connection even while no turn is
                    # queued; a disconnected worker must not keep taking work.
                    conn.execute("SELECT 1")
                    close_old_connections()
                    current = AnalysisTurn.objects.filter(status=AnalysisTurn.Status.QUEUED).select_related("conversation", "snapshot").order_by("created_at").first()
                    if current:
                        current.status = AnalysisTurn.Status.RUNNING
                        current.progress = "正在准备数据快照"
                        current.started_at = current.started_at or timezone.now()
                        current.attempts += 1
                        current.save(update_fields=["status", "progress", "started_at", "attempts"])
                        if current.attempts > 3:
                            mark_failed(current, "本轮多次中断，请重新提交问题。")
                        else:
                            signal.setitimer(signal.ITIMER_REAL, settings.TRADING_ASSISTANT_MAX_RUN_SECONDS)
                            try:
                                report = run_agent(current, saver)
                                mark_success(current, report)
                                self.stdout.write(f"完成分析 {current.pk}")
                            except TimeoutError:
                                mark_failed(current, "本轮分析超时，问题与已完成的工具结果已保存。可重新提问。")
                            except ReportGenerationError as exc:
                                mark_failed(current, str(exc))
                            except (httpx.HTTPError, ConnectionError):
                                mark_failed(current, "模型服务连接失败，请检查模型服务与网络配置后重新提问。")
                            except Exception as exc:
                                # Provider exception strings may contain request bodies or credentials.
                                mark_failed(current, "分析执行出现异常，问题与已完成的工具结果已保留。请稍后重新提问。")
                                self.stderr.write(f"分析 {current.pk} 失败：{type(exc).__name__}")
                            finally:
                                signal.setitimer(signal.ITIMER_REAL, 0)
                        current = None
                    if options["once"]:
                        break
                    stop.wait(2)
        except KeyboardInterrupt:
            if current:
                AnalysisTurn.objects.filter(pk=current.pk, status=AnalysisTurn.Status.RUNNING).update(
                    status=AnalysisTurn.Status.QUEUED, progress="后台已暂停，重启后继续",
                )
        finally:
            stop.set()
            if pulse:
                pulse.join(timeout=6)
                try:
                    WorkerHeartbeat.objects.filter(name="default").delete()
                except DatabaseError:
                    pass  # A broken tunnel must not mask the original failure.
            signal.setitimer(signal.ITIMER_REAL, 0)
            for sig, old in old_handlers.items():
                signal.signal(sig, old)
