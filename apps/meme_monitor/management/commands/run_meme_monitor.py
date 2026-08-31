import os
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.meme_monitor.data_source import GeckoTerminalDataSource, MarketDataSourceError
from apps.meme_monitor.detector import MemeAnomalyDetector, MemeDetectorConfig
from apps.meme_monitor.models import MemeMonitorRun
from apps.meme_monitor.research import (
    MemeContinuationResearchConfig,
    MemeContinuationResearchService,
)
from apps.meme_monitor.service import MemeMonitorConfig, MemeMonitorService
from apps.meme_monitor.storage import DjangoMemeMonitorStorage


class Command(BaseCommand):
    help = "Continuously discover new DEX pairs and record Meme market anomalies."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run one discovery/market/detection cycle, then exit.",
        )

    def handle(self, *args, **options):
        storage = DjangoMemeMonitorStorage()
        run = storage.start_run(
            chain=settings.MEME_MONITOR_CHAIN,
            mode=(
                MemeMonitorRun.Mode.ONCE
                if options["once"]
                else MemeMonitorRun.Mode.CONTINUOUS
            ),
            process_id=os.getpid(),
            started_at=timezone.now(),
        )
        data_source = GeckoTerminalDataSource(
            network=settings.MEME_MONITOR_NETWORK,
            chain=settings.MEME_MONITOR_CHAIN,
            base_url=settings.MEME_MONITOR_GECKOTERMINAL_BASE_URL,
            timeout_seconds=settings.MEME_MONITOR_HTTP_TIMEOUT_SECONDS,
            max_retries=settings.MEME_MONITOR_HTTP_MAX_RETRIES,
            min_request_interval_seconds=(
                settings.MEME_MONITOR_MIN_REQUEST_INTERVAL_SECONDS
            ),
            proxy_url=settings.MEME_MONITOR_PROXY_URL,
        )
        detector = MemeAnomalyDetector(
            MemeDetectorConfig(
                price_change_5m_pct=Decimal(
                    str(settings.MEME_MONITOR_PRICE_CHANGE_5M_PCT)
                ),
                minimum_volume_5m_usd=Decimal(
                    str(settings.MEME_MONITOR_MIN_VOLUME_5M_USD)
                ),
                volume_spike_multiplier=Decimal(
                    str(settings.MEME_MONITOR_VOLUME_SPIKE_MULTIPLIER)
                ),
                volume_history_min_samples=(
                    settings.MEME_MONITOR_VOLUME_HISTORY_MIN_SAMPLES
                ),
                minimum_transactions_5m=(settings.MEME_MONITOR_MIN_TRANSACTIONS_5M),
                minimum_liquidity_usd=Decimal(
                    str(settings.MEME_MONITOR_MIN_LIQUIDITY_USD)
                ),
            )
        )
        service = MemeMonitorService(
            data_source=data_source,
            storage=storage,
            detector=detector,
            config=MemeMonitorConfig(
                chain=settings.MEME_MONITOR_CHAIN,
                new_pair_max_age_hours=settings.MEME_MONITOR_NEW_PAIR_MAX_AGE_HOURS,
                poll_interval_seconds=settings.MEME_MONITOR_POLL_INTERVAL_SECONDS,
                cooldown_seconds=settings.MEME_MONITOR_COOLDOWN_SECONDS,
                bootstrap_discovery_pages=(
                    settings.MEME_MONITOR_BOOTSTRAP_DISCOVERY_PAGES
                ),
                max_tracked_pairs=settings.MEME_MONITOR_MAX_TRACKED_PAIRS,
                volume_history_samples=settings.MEME_MONITOR_VOLUME_HISTORY_SAMPLES,
            ),
            research=MemeContinuationResearchService(
                MemeContinuationResearchConfig(
                    rule_version=settings.MEME_RESEARCH_RULE_VERSION,
                    entry_delay_seconds=settings.MEME_RESEARCH_ENTRY_DELAY_SECONDS,
                    horizon_seconds=settings.MEME_RESEARCH_HORIZON_SECONDS,
                    observation_tolerance_seconds=(
                        settings.MEME_RESEARCH_OBSERVATION_TOLERANCE_SECONDS
                    ),
                    notional_usd=Decimal(str(settings.MEME_RESEARCH_NOTIONAL_USD)),
                    fee_bps_per_side=Decimal(
                        str(settings.MEME_RESEARCH_FEE_BPS_PER_SIDE)
                    ),
                    max_price_impact_pct=Decimal(
                        str(settings.MEME_RESEARCH_MAX_PRICE_IMPACT_PCT)
                    ),
                )
            ),
            monitor_run_id=run.pk,
        )
        try:
            if options["once"]:
                service.run_once()
            else:
                service.run_forever()
        except MarketDataSourceError as exc:
            storage.finish_run(
                run.pk,
                status=MemeMonitorRun.Status.FAILED,
                stopped_at=timezone.now(),
                error_message=f"{type(exc).__name__}: {exc}",
            )
            raise CommandError(f"Meme market source failed: {exc}") from exc
        except KeyboardInterrupt:
            storage.finish_run(
                run.pk,
                status=MemeMonitorRun.Status.STOPPED,
                stopped_at=timezone.now(),
            )
            self.stdout.write(self.style.WARNING("Meme monitor stopped."))
        except Exception as exc:
            storage.finish_run(
                run.pk,
                status=MemeMonitorRun.Status.FAILED,
                stopped_at=timezone.now(),
                error_message=f"{type(exc).__name__}: {exc}",
            )
            raise
        else:
            storage.finish_run(
                run.pk,
                status=MemeMonitorRun.Status.STOPPED,
                stopped_at=timezone.now(),
            )
        finally:
            data_source.close()
