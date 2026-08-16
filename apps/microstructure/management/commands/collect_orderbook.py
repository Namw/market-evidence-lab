from __future__ import annotations

import asyncio
import signal

from django.core.management.base import BaseCommand, CommandError

from apps.microstructure.collector import OrderBookCollector


class Command(BaseCommand):
    help = "Collect Binance ETHUSDT Top20 order-book data into one-second snapshots."

    def add_arguments(self, parser):
        parser.add_argument(
            "--symbol",
            help="Override MICROSTRUCTURE_SYMBOL for this run.",
        )

    async def _run(self, collector: OrderBookCollector) -> None:
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
            await collector.run(stop_event)
        finally:
            stop_event.set()
            for signal_number in registered_signals:
                loop.remove_signal_handler(signal_number)

    def handle(self, *args, **options):
        try:
            collector = OrderBookCollector.from_settings(symbol=options.get("symbol"))
            self.stdout.write(f"Collecting {collector.stream_url}; press Ctrl+C to stop.")
            asyncio.run(self._run(collector))
        except KeyboardInterrupt:
            pass
        except Exception as exc:
            raise CommandError(
                f"Order-book collection stopped: {exc.__class__.__name__}: {exc}"
            ) from exc
        self.stdout.write(
            self.style.SUCCESS(
                "Order-book collection stopped; "
                f"messages={collector.received_messages}; "
                f"snapshots={collector.saved_snapshots}; "
                f"reconnects={collector.reconnect_count}."
            )
        )
