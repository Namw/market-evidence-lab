from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from django.utils import timezone

from apps.meme_monitor.data_source.base import MemeMarketDataSource
from apps.meme_monitor.detector import MemeAnomalyDetector
from apps.meme_monitor.domain import MemeAnomalyEvent, TokenMarketSnapshot
from apps.meme_monitor.research import MemeContinuationResearchService
from apps.meme_monitor.storage import DjangoMemeMonitorStorage

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MemeMonitorConfig:
    chain: str
    new_pair_max_age_hours: float
    poll_interval_seconds: float
    cooldown_seconds: float
    bootstrap_discovery_pages: int
    max_tracked_pairs: int
    volume_history_samples: int


@dataclass(frozen=True, slots=True)
class MonitorCycleResult:
    fetched_pairs: int
    tracked_pairs: int
    saved_snapshots: int
    detected_anomalies: int
    warning_message: str = ""


class MemeMonitorService:
    def __init__(
        self,
        *,
        data_source: MemeMarketDataSource,
        storage: DjangoMemeMonitorStorage,
        detector: MemeAnomalyDetector,
        config: MemeMonitorConfig,
        research: MemeContinuationResearchService | None = None,
        monitor_run_id: int | None = None,
        sleep=time.sleep,
    ) -> None:
        self.data_source = data_source
        self.storage = storage
        self.detector = detector
        self.config = config
        self.research = research
        self.monitor_run_id = monitor_run_id
        self._sleep = sleep
        self._tracked: dict[str, TokenMarketSnapshot] = {}
        self._initialized = False

    def run_forever(self) -> None:
        logger.info(
            "starting meme monitor: chain=%s poll=%ss max_age=%sh max_pairs=%s",
            self.config.chain,
            self.config.poll_interval_seconds,
            self.config.new_pair_max_age_hours,
            self.config.max_tracked_pairs,
        )
        while True:
            started = time.monotonic()
            try:
                self.run_once()
            except Exception:
                logger.exception("monitor cycle failed; continuing next cycle")
            elapsed = time.monotonic() - started
            self._sleep(max(0, self.config.poll_interval_seconds - elapsed))

    def run_once(self, *, observed_at: datetime | None = None) -> MonitorCycleResult:
        now = observed_at or timezone.now()
        cycle_id: int | None = None
        if self.monitor_run_id is not None:
            cycle_id = self.storage.start_cycle(
                self.monitor_run_id,
                started_at=now,
            ).pk
        try:
            result = self._run_cycle(now)
        except Exception as exc:
            if self.monitor_run_id is not None and cycle_id is not None:
                self.storage.fail_cycle(
                    self.monitor_run_id,
                    cycle_id,
                    finished_at=timezone.now(),
                    error_message=f"{type(exc).__name__}: {exc}",
                )
            raise
        if self.monitor_run_id is not None and cycle_id is not None:
            self.storage.finish_cycle(
                self.monitor_run_id,
                cycle_id,
                finished_at=timezone.now(),
                fetched_pairs=result.fetched_pairs,
                tracked_pairs=result.tracked_pairs,
                saved_snapshots=result.saved_snapshots,
                detected_anomalies=result.detected_anomalies,
                warning_message=result.warning_message,
            )
        return result

    def _run_cycle(self, now: datetime) -> MonitorCycleResult:
        begin_cycle = getattr(self.data_source, "begin_cycle", None)
        if begin_cycle is not None:
            begin_cycle()
        cutoff = now - timedelta(hours=self.config.new_pair_max_age_hours)
        if not self._initialized:
            for snapshot in self.storage.recent_pairs(
                chain=self.config.chain,
                created_since=cutoff,
                limit=self.config.max_tracked_pairs,
            ):
                self._tracked[_pair_key(snapshot.pair_address)] = snapshot

        discovery_pages = (
            self.config.bootstrap_discovery_pages if not self._initialized else 1
        )
        discovered = self.data_source.discover_new_pairs(
            observed_at=now,
            max_age_hours=self.config.new_pair_max_age_hours,
            max_pages=discovery_pages,
        )
        self._initialized = True
        logger.info("fetched %s new-pair candidates", len(discovered))
        for snapshot in discovered:
            self._tracked[_pair_key(snapshot.pair_address)] = snapshot
        self._prune_tracked(cutoff=cutoff)
        logger.info("tracking %s pairs", len(self._tracked))

        pair_addresses = {item.pair_address for item in self._tracked.values()}
        if self.research is not None:
            pair_addresses.update(
                self.research.tracked_pair_addresses(chain=self.config.chain)
            )
        snapshots = self.data_source.fetch_market_snapshots(
            list(pair_addresses),
            observed_at=now,
        )
        if self.research is not None:
            migration_destinations = (
                self.research.migration_destinations(snapshots) - pair_addresses
            )
            if migration_destinations:
                snapshots.extend(
                    self.data_source.fetch_market_snapshots(
                        list(migration_destinations),
                        observed_at=now,
                    )
                )
                pair_addresses.update(migration_destinations)
        valid_snapshots = [item for item in snapshots if item.pair_created_at >= cutoff]
        if self.research is not None:
            self.research.observe_launchpads(valid_snapshots)
        histories = self.storage.volume_histories(
            chain=self.config.chain,
            pair_addresses=[item.pair_address for item in valid_snapshots],
            limit=self.config.volume_history_samples,
        )

        detected = 0
        for snapshot in valid_snapshots:
            try:
                event = self._detect_one(
                    snapshot,
                    event_time=now,
                    historical_volumes=histories.get(snapshot.pair_address, []),
                )
                if event is None:
                    continue
                event_record = self.storage.save_event(event)
                if self.research is not None:
                    self.research.open_first_episode(event_record)
                detected += 1
                logger.warning("%s", format_anomaly_event(event))
            except Exception:
                logger.exception(
                    "failed to detect/save pair %s; continuing",
                    snapshot.pair_address,
                )
        logger.info("detected %s anomalies", detected)
        if self.research is not None:
            self.research.advance(valid_snapshots, observed_at=now)
        updated_states = self.storage.upsert_pair_states(
            valid_snapshots,
            volume_history_limit=self.config.volume_history_samples,
        )
        logger.info("updated %s pair states", updated_states)
        drain_warnings = getattr(self.data_source, "drain_warnings", None)
        warnings = drain_warnings() if drain_warnings is not None else []
        warning_message = " | ".join(warnings)[:500]
        return MonitorCycleResult(
            fetched_pairs=len(discovered),
            tracked_pairs=len(pair_addresses),
            saved_snapshots=updated_states,
            detected_anomalies=detected,
            warning_message=warning_message,
        )

    def _detect_one(
        self,
        snapshot: TokenMarketSnapshot,
        *,
        event_time: datetime,
        historical_volumes: list[Decimal],
    ) -> MemeAnomalyEvent | None:
        event = self.detector.detect(
            snapshot,
            historical_volumes=historical_volumes,
            event_time=event_time,
        )
        if event is None:
            return None
        cooldown_since = event_time - timedelta(seconds=self.config.cooldown_seconds)
        if self.storage.in_cooldown(
            chain=event.chain,
            token_address=event.token_address,
            anomaly_type=event.anomaly_type,
            since=cooldown_since,
        ):
            return None
        return event

    def _prune_tracked(self, *, cutoff: datetime) -> None:
        recent = [
            item for item in self._tracked.values() if item.pair_created_at >= cutoff
        ]
        recent.sort(key=lambda item: item.pair_created_at, reverse=True)
        self._tracked = {
            _pair_key(item.pair_address): item
            for item in recent[: self.config.max_tracked_pairs]
        }


def _pair_key(address: str) -> str:
    return address


def format_anomaly_event(event: MemeAnomalyEvent) -> str:
    return "\n".join(
        [
            "[Meme Anomaly]",
            f"Event ID: {event.event_id}",
            f"Name: {event.name or '-'}",
            f"Symbol: {event.symbol or '-'}",
            f"Chain: {event.chain}",
            f"Age: {event.pair_age_minutes} min",
            f"Price: {_money(event.price_usd, decimals=12)}",
            f"Price change 5m: {_percent(event.price_change_5m)}",
            f"Price change 1h: {_percent(event.price_change_1h)}",
            f"Volume 5m: {_money(event.volume_5m)}",
            f"Liquidity: {_money(event.liquidity_usd)}",
            f"Buys 5m: {event.buys_5m if event.buys_5m is not None else '-'}",
            f"Sells 5m: {event.sells_5m if event.sells_5m is not None else '-'}",
            "Triggered:",
            *[f"- {rule}" for rule in event.triggered_rules],
        ]
    )


def _money(value: Decimal | None, *, decimals: int = 2) -> str:
    if value is None:
        return "-"
    return f"${value:,.{decimals}f}"


def _percent(value: Decimal | None) -> str:
    if value is None:
        return "-"
    return f"{value:+,.2f}%"
