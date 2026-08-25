from __future__ import annotations

import asyncio
import signal

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.microstructure.collector import OrderBookCollector
from apps.microstructure.models import MicrostructureCollectorRun


class Command(BaseCommand):
    help = "Collect Binance perpetual 1m klines and Top20 depth into minute facts."

    def add_arguments(self, parser):
        parser.add_argument(
            "--symbol",
            help="Override MICROSTRUCTURE_SYMBOL for this run.",
        )
        parser.add_argument(
            "--run-id",
            type=int,
            help="Collector run row used by the web control page.",
        )

    @staticmethod
    def _serialize_levels(levels) -> list[dict[str, str]]:
        return [
            {"price": str(level.price), "quantity": str(level.quantity)}
            for level in levels
        ]

    @staticmethod
    def _write_progress(
        run_id: int,
        collector: OrderBookCollector,
    ) -> None:
        latest = collector.latest
        latest_event_time = max(
            (
                value
                for value in (
                    latest.event_time if latest else None,
                    collector.latest_kline.event_time if collector.latest_kline else None,
                )
                if value is not None
            ),
            default=None,
        )
        MicrostructureCollectorRun.objects.filter(pk=run_id).update(
            connection_state=collector.connection_state,
            received_messages=collector.received_messages,
            saved_minute_updates=collector.saved_minute_updates,
            reconnect_count=collector.reconnect_count,
            latest_event_time=latest_event_time,
            latest_sampled_at=collector.latest_sampled_at,
            latest_update_id=latest.update_id if latest else None,
            latest_bids=Command._serialize_levels(latest.bids) if latest else [],
            latest_asks=Command._serialize_levels(latest.asks) if latest else [],
            heartbeat_at=timezone.now(),
            error_message=collector.last_error,
        )

    async def _heartbeat(
        self,
        stop_event: asyncio.Event,
        run_id: int,
        collector: OrderBookCollector,
    ) -> None:
        while not stop_event.is_set():
            await asyncio.to_thread(self._write_progress, run_id, collector)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=1)
            except TimeoutError:
                continue

    async def _run(
        self,
        collector: OrderBookCollector,
        *,
        run_id: int | None,
    ) -> None:
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        registered_signals: list[signal.Signals] = []
        for signal_number in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signal_number, stop_event.set)
            except NotImplementedError:
                continue
            registered_signals.append(signal_number)
        try:
            if run_id is None:
                await collector.run(stop_event)
            else:
                async with asyncio.TaskGroup() as tasks:
                    tasks.create_task(collector.run(stop_event))
                    tasks.create_task(self._heartbeat(stop_event, run_id, collector))
        finally:
            stop_event.set()
            for signal_number in registered_signals:
                loop.remove_signal_handler(signal_number)

    def handle(self, *args, **options):
        run_id = options.get("run_id")
        run = None
        collector = None
        try:
            if run_id is not None:
                run = MicrostructureCollectorRun.objects.get(pk=run_id)
                now = timezone.now()
                MicrostructureCollectorRun.objects.filter(pk=run_id).update(
                    status=MicrostructureCollectorRun.Status.RUNNING,
                    connection_state=MicrostructureCollectorRun.ConnectionState.CONNECTING,
                    started_at=run.started_at or now,
                    heartbeat_at=now,
                    stopped_at=None,
                    error_message="",
                )
            collector = OrderBookCollector.from_settings(symbol=options.get("symbol"))
            self.stdout.write(f"Collecting {collector.stream_url}; press Ctrl+C to stop.")
            asyncio.run(self._run(collector, run_id=run_id))
        except KeyboardInterrupt:
            pass
        except Exception as exc:
            if run_id is not None:
                MicrostructureCollectorRun.objects.filter(pk=run_id).update(
                    status=MicrostructureCollectorRun.Status.FAILED,
                    connection_state=MicrostructureCollectorRun.ConnectionState.DISCONNECTED,
                    stopped_at=timezone.now(),
                    error_message=f"{exc.__class__.__name__}: collection stopped"[:1_000],
                )
            raise CommandError(
                f"Order-book collection stopped: {exc.__class__.__name__}: {exc}"
            ) from exc
        if run_id is not None:
            self._write_progress(run_id, collector)
            MicrostructureCollectorRun.objects.filter(pk=run_id).update(
                status=MicrostructureCollectorRun.Status.STOPPED,
                connection_state=MicrostructureCollectorRun.ConnectionState.DISCONNECTED,
                stopped_at=timezone.now(),
            )
        self.stdout.write(
            self.style.SUCCESS(
                "Order-book collection stopped; "
                f"messages={collector.received_messages}; "
                f"minute_updates={collector.saved_minute_updates}; "
                f"reconnects={collector.reconnect_count}."
            )
        )
