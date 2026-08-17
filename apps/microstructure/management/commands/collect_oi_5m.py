from __future__ import annotations

import signal
import threading
from datetime import UTC, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.collection.derivatives import _save_oi_batch
from apps.market_data.derivatives import BinanceOpenInterestClient
from apps.market_data.models import OpenInterest
from apps.microstructure.calculations import floor_time
from apps.microstructure.models import MicrostructureCollectorRun

OI_PERIOD = OpenInterest.Period.FIVE_MINUTES
OI_STEP = timedelta(minutes=5)


class Command(BaseCommand):
    help = "Collect the latest Binance 5m open interest every five minutes."

    def add_arguments(self, parser):
        parser.add_argument(
            "--symbol",
            help="Override MICROSTRUCTURE_SYMBOL for this run.",
        )
        parser.add_argument(
            "--run-id",
            type=int,
            help="MicrostructureCollectorRun row whose lifecycle owns this process.",
        )

    def _collect_once(
        self,
        client: BinanceOpenInterestClient,
        symbol: str,
        run_id: int | None,
    ) -> bool:
        """Collect the just-finished 5m OI period. Returns True on success."""
        now = timezone.now().astimezone(UTC)
        period_end = floor_time(now, seconds=300)
        period_start = period_end - OI_STEP
        collected = False
        try:
            for batch in client.iter_batches(
                symbol=symbol,
                period=OI_PERIOD,
                range_start=period_start,
                range_end=period_end,
            ):
                _save_oi_batch(symbol=symbol, period=OI_PERIOD, payloads=batch)
                collected = True
        except Exception as exc:
            self.stderr.write(
                f"OI collection failed ({exc.__class__.__name__}): {exc}"
            )
        if run_id is not None:
            MicrostructureCollectorRun.objects.filter(pk=run_id).update(
                heartbeat_at=timezone.now(),
                error_message="" if collected else f"OI 采集失败: {period_end.isoformat()}",
            )
        return collected

    def handle(self, *args, **options):
        symbol = options["symbol"] or settings.MICROSTRUCTURE_SYMBOL
        run_id = options["run_id"]
        stop_event = threading.Event()

        def request_stop(signum, frame):
            stop_event.set()

        previous_handlers = {}
        for signal_number in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signal_number] = signal.getsignal(signal_number)
            signal.signal(signal_number, request_stop)

        client = BinanceOpenInterestClient()
        try:
            # 立即补采一次（上一个完整周期），然后对齐到下一个 5 分钟边界
            self._collect_once(client, symbol, run_id)
            while not stop_event.is_set():
                now = timezone.now().astimezone(UTC)
                next_boundary = floor_time(now, seconds=300) + OI_STEP
                wait_seconds = max(
                    1.0, (next_boundary - now).total_seconds() + 2
                )
                stop_event.wait(wait_seconds)
                if stop_event.is_set():
                    break
                self._collect_once(client, symbol, run_id)
        finally:
            client.close()
            for signal_number, previous_handler in previous_handlers.items():
                signal.signal(signal_number, previous_handler)
        self.stdout.write("5m OI collector stopped.")
