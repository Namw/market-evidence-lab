from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction

from apps.meme_monitor.domain import TokenMarketSnapshot
from apps.meme_monitor.models import (
    MemeAnomalyEventRecord,
    MemeContinuationResearchEpisode,
    MemeLaunchpadTokenState,
)


@dataclass(frozen=True, slots=True)
class MemeContinuationResearchConfig:
    rule_version: str
    entry_delay_seconds: int
    horizon_seconds: int
    observation_tolerance_seconds: int
    notional_usd: Decimal
    fee_bps_per_side: Decimal
    max_price_impact_pct: Decimal


class MemeContinuationResearchService:
    """Build first-anomaly, migration-aware, executable five-minute episodes."""

    def __init__(self, config: MemeContinuationResearchConfig) -> None:
        self.config = config

    def open_first_episode(
        self,
        event: MemeAnomalyEventRecord,
    ) -> MemeContinuationResearchEpisode | None:
        launchpad_state = MemeLaunchpadTokenState.objects.filter(
            source=event.source,
            chain=event.chain,
            token_address=event.token_address,
        ).first()
        if launchpad_state is None:
            return None

        destination = launchpad_state.migrated_destination_pair_address
        current_pair = destination or event.pair_address
        defaults = {
            "trigger_event": event,
            "symbol": event.symbol,
            "name": event.name,
            "status": MemeContinuationResearchEpisode.Status.WAITING_ENTRY,
            "triggered_at": event.event_time,
            "launchpad_pair_address": launchpad_state.launchpad_pair_address,
            "trigger_pair_address": event.pair_address,
            "current_pair_address": current_pair,
            "migrated_destination_pair_address": destination,
            "migration_detected_at": (
                launchpad_state.completed_at
                if destination
                else None
            ),
            "entry_target_at": event.event_time
            + timedelta(seconds=self.config.entry_delay_seconds),
            "notional_usd": self.config.notional_usd,
            "fee_bps_per_side": self.config.fee_bps_per_side,
        }
        lookup = {
            "source": event.source,
            "chain": event.chain,
            "token_address": event.token_address,
            "rule_version": self.config.rule_version,
        }
        try:
            with transaction.atomic():
                episode, _ = MemeContinuationResearchEpisode.objects.get_or_create(
                    **lookup,
                    defaults=defaults,
                )
        except IntegrityError:
            episode = MemeContinuationResearchEpisode.objects.get(**lookup)
        return episode

    def observe_launchpads(
        self,
        snapshots: Sequence[TokenMarketSnapshot],
    ) -> None:
        for snapshot in snapshots:
            if snapshot.launchpad_completed is None:
                continue
            destination = snapshot.migrated_destination_pair_address
            MemeLaunchpadTokenState.objects.update_or_create(
                source="geckoterminal",
                chain=snapshot.chain,
                token_address=snapshot.token_address,
                defaults={
                    "symbol": snapshot.symbol,
                    "name": snapshot.name,
                    "launchpad_pair_address": snapshot.pair_address,
                    "current_pair_address": destination or snapshot.pair_address,
                    "migrated_destination_pair_address": destination,
                    "graduation_percentage": (
                        snapshot.launchpad_graduation_percentage
                    ),
                    "completed": snapshot.launchpad_completed,
                    "completed_at": snapshot.launchpad_completed_at,
                    "observed_at": snapshot.timestamp,
                },
            )

    def tracked_pair_addresses(self, *, chain: str) -> set[str]:
        addresses: set[str] = set()
        rows = MemeContinuationResearchEpisode.objects.filter(
            chain=chain,
            status__in=(
                MemeContinuationResearchEpisode.Status.WAITING_ENTRY,
                MemeContinuationResearchEpisode.Status.WAITING_EXIT,
            ),
        ).values_list(
            "launchpad_pair_address",
            "current_pair_address",
            "migrated_destination_pair_address",
        )
        for row in rows:
            addresses.update(address for address in row if address)
        return addresses

    def migration_destinations(
        self,
        snapshots: Sequence[TokenMarketSnapshot],
    ) -> set[str]:
        return {
            item.migrated_destination_pair_address
            for item in snapshots
            if item.migrated_destination_pair_address
        }

    def advance(
        self,
        snapshots: Sequence[TokenMarketSnapshot],
        *,
        observed_at: datetime,
    ) -> None:
        by_token: dict[str, list[TokenMarketSnapshot]] = defaultdict(list)
        for snapshot in snapshots:
            by_token[_address_key(snapshot.token_address)].append(snapshot)

        episodes = MemeContinuationResearchEpisode.objects.filter(
            status__in=(
                MemeContinuationResearchEpisode.Status.WAITING_ENTRY,
                MemeContinuationResearchEpisode.Status.WAITING_EXIT,
            )
        )
        for episode in episodes:
            token_snapshots = by_token.get(_address_key(episode.token_address), [])
            changed_fields = self._record_migration(episode, token_snapshots)
            if episode.status == MemeContinuationResearchEpisode.Status.WAITING_ENTRY:
                changed_fields.update(
                    self._observe_entry(
                        episode,
                        token_snapshots,
                        observed_at=observed_at,
                    )
                )
            if episode.status == MemeContinuationResearchEpisode.Status.WAITING_EXIT:
                changed_fields.update(
                    self._observe_exit(
                        episode,
                        token_snapshots,
                        observed_at=observed_at,
                    )
                )
            if changed_fields:
                episode.save(update_fields=sorted(changed_fields | {"updated_at"}))

    def _record_migration(
        self,
        episode: MemeContinuationResearchEpisode,
        snapshots: Sequence[TokenMarketSnapshot],
    ) -> set[str]:
        changed: set[str] = set()
        migration = next(
            (
                item
                for item in snapshots
                if item.migrated_destination_pair_address
            ),
            None,
        )
        if migration is None:
            return changed
        destination = migration.migrated_destination_pair_address
        if episode.migrated_destination_pair_address != destination:
            episode.migrated_destination_pair_address = destination
            changed.add("migrated_destination_pair_address")
        if episode.current_pair_address != destination:
            episode.current_pair_address = destination
            changed.add("current_pair_address")
        if episode.migration_detected_at is None:
            episode.migration_detected_at = (
                migration.launchpad_completed_at or migration.timestamp
            )
            changed.add("migration_detected_at")
        return changed

    def _observe_entry(
        self,
        episode: MemeContinuationResearchEpisode,
        snapshots: Sequence[TokenMarketSnapshot],
        *,
        observed_at: datetime,
    ) -> set[str]:
        if observed_at < episode.entry_target_at:
            return set()
        deadline = episode.entry_target_at + timedelta(
            seconds=self.config.observation_tolerance_seconds
        )
        candidate = self._select_executable_snapshot(
            episode,
            snapshots,
            notional_usd=episode.notional_usd,
        )
        if candidate is not None and candidate.timestamp <= deadline:
            impact = _price_impact_pct(
                episode.notional_usd,
                candidate.liquidity_usd,
            )
            episode.entry_observed_at = candidate.timestamp
            episode.entry_pair_address = candidate.pair_address
            episode.entry_price_usd = candidate.price_usd
            episode.entry_liquidity_usd = candidate.liquidity_usd
            episode.entry_price_impact_pct = impact
            episode.current_pair_address = candidate.pair_address
            episode.exit_target_at = candidate.timestamp + timedelta(
                seconds=self.config.horizon_seconds
            )
            episode.status = MemeContinuationResearchEpisode.Status.WAITING_EXIT
            return {
                "entry_observed_at",
                "entry_pair_address",
                "entry_price_usd",
                "entry_liquidity_usd",
                "entry_price_impact_pct",
                "current_pair_address",
                "exit_target_at",
                "status",
            }
        if observed_at > deadline:
            episode.status = MemeContinuationResearchEpisode.Status.UNAVAILABLE
            episode.failure_reason = "entry_not_executable_within_tolerance"
            return {"status", "failure_reason"}
        return set()

    def _observe_exit(
        self,
        episode: MemeContinuationResearchEpisode,
        snapshots: Sequence[TokenMarketSnapshot],
        *,
        observed_at: datetime,
    ) -> set[str]:
        if episode.exit_target_at is None or observed_at < episode.exit_target_at:
            return set()
        deadline = episode.exit_target_at + timedelta(
            seconds=self.config.observation_tolerance_seconds
        )
        if episode.entry_price_usd is None or episode.entry_price_usd <= 0:
            episode.status = MemeContinuationResearchEpisode.Status.UNAVAILABLE
            episode.failure_reason = "invalid_entry_price"
            return {"status", "failure_reason"}

        candidate = self._select_executable_snapshot(
            episode,
            snapshots,
            notional_usd=episode.notional_usd,
            entry_price_usd=episode.entry_price_usd,
        )
        if candidate is not None and candidate.timestamp <= deadline:
            gross_ratio = candidate.price_usd / episode.entry_price_usd
            exit_notional = episode.notional_usd * gross_ratio
            exit_impact = _price_impact_pct(
                exit_notional,
                candidate.liquidity_usd,
            )
            fee_fraction = episode.fee_bps_per_side / Decimal(10_000)
            entry_impact_fraction = episode.entry_price_impact_pct / Decimal(100)
            exit_impact_fraction = exit_impact / Decimal(100)
            net_ratio = (
                gross_ratio
                * (Decimal(1) - entry_impact_fraction)
                * (Decimal(1) - fee_fraction)
                * (Decimal(1) - exit_impact_fraction)
                * (Decimal(1) - fee_fraction)
            )
            episode.exit_observed_at = candidate.timestamp
            episode.exit_pair_address = candidate.pair_address
            episode.exit_price_usd = candidate.price_usd
            episode.exit_liquidity_usd = candidate.liquidity_usd
            episode.exit_price_impact_pct = exit_impact
            episode.current_pair_address = candidate.pair_address
            episode.gross_return_pct = (gross_ratio - Decimal(1)) * Decimal(100)
            episode.net_return_pct = (net_ratio - Decimal(1)) * Decimal(100)
            episode.status = MemeContinuationResearchEpisode.Status.COMPLETED
            return {
                "exit_observed_at",
                "exit_pair_address",
                "exit_price_usd",
                "exit_liquidity_usd",
                "exit_price_impact_pct",
                "current_pair_address",
                "gross_return_pct",
                "net_return_pct",
                "status",
            }
        if observed_at > deadline:
            episode.status = MemeContinuationResearchEpisode.Status.UNAVAILABLE
            episode.failure_reason = "exit_not_executable_within_tolerance"
            return {"status", "failure_reason"}
        return set()

    def _select_executable_snapshot(
        self,
        episode: MemeContinuationResearchEpisode,
        snapshots: Sequence[TokenMarketSnapshot],
        *,
        notional_usd: Decimal,
        entry_price_usd: Decimal | None = None,
    ) -> TokenMarketSnapshot | None:
        candidates = [
            item
            for item in snapshots
            if item.price_usd is not None
            and item.price_usd > 0
            and item.liquidity_usd is not None
            and item.liquidity_usd > 0
        ]
        destination = episode.migrated_destination_pair_address
        if destination:
            candidates = [
                item
                for item in candidates
                if _address_key(item.pair_address) == _address_key(destination)
            ]
        executable: list[TokenMarketSnapshot] = []
        for item in candidates:
            observation_notional = notional_usd
            if entry_price_usd is not None:
                observation_notional *= item.price_usd / entry_price_usd
            if (
                _price_impact_pct(observation_notional, item.liquidity_usd)
                <= self.config.max_price_impact_pct
            ):
                executable.append(item)
        return max(
            executable,
            key=lambda item: item.liquidity_usd or Decimal(0),
            default=None,
        )


def _price_impact_pct(
    notional_usd: Decimal,
    liquidity_usd: Decimal | None,
) -> Decimal:
    if liquidity_usd is None or liquidity_usd <= 0:
        return Decimal(100)
    quote_side_reserve = liquidity_usd / Decimal(2)
    return (
        notional_usd / (quote_side_reserve + notional_usd) * Decimal(100)
    )


def _address_key(address: str) -> str:
    return address.lower()
