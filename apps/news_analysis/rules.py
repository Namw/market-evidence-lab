from __future__ import annotations

import re
from dataclasses import dataclass

from apps.news_data.models import NewsRawRecord
from apps.news_data.sources import BINANCE_ANNOUNCEMENTS_CODE

from .models import NewsAnalysisResult


@dataclass(frozen=True, slots=True)
class RuleDecision:
    rule_id: str
    observation_result: str
    event_type: str
    impact_scope: str
    importance: str
    rationale: str
    confidence: str


@dataclass(frozen=True, slots=True)
class TitleRule:
    rule_id: str
    pattern: re.Pattern[str]
    rationale: str


BINANCE_TITLE_RULES = (
    TitleRule(
        "binance_marketing_quiz_v1",
        re.compile(r"\b(?:quiz|word of the day|wotd|learn\s*(?:&|and)\s*earn)\b", re.I),
        "标题明确属于答题或学习奖励活动，对当前市场研究缺少独立观察价值。",
    ),
    TitleRule(
        "binance_marketing_referral_v1",
        re.compile(
            r"\b(?:referral\s+(?:campaign|program|promotion)|refer\s+(?:friends?|users?).{0,35}\bearn)\b",
            re.I,
        ),
        "标题明确属于推荐返佣或拉新奖励活动，对当前市场研究缺少独立观察价值。",
    ),
    TitleRule(
        "binance_marketing_competition_v1",
        re.compile(r"\b(?:trading\s+(?:competition|tournament)|trade.{0,30}\bwin\b)\b", re.I),
        "标题明确属于交易竞赛，对当前市场研究缺少独立观察价值。",
    ),
    TitleRule(
        "binance_marketing_rewards_v1",
        re.compile(
            r"\b(?:giveaway|lucky\s+draw|complete\s+(?:the\s+)?tasks?.{0,40}(?:reward|prize)|share\s+.{0,25}\brewards?\b)\b",
            re.I,
        ),
        "标题明确属于抽奖、奖励任务或奖品活动，对当前市场研究缺少独立观察价值。",
    ),
)


def match_fixed_rule(record: NewsRawRecord) -> RuleDecision | None:
    """Return only source-specific, high-certainty marketing decisions."""
    if record.source.code != BINANCE_ANNOUNCEMENTS_CODE:
        return None
    title = " ".join(record.title.split())
    for rule in BINANCE_TITLE_RULES:
        if rule.pattern.search(title):
            return RuleDecision(
                rule_id=rule.rule_id,
                observation_result=NewsAnalysisResult.ObservationResult.NOISE,
                event_type=NewsAnalysisResult.EventType.MARKETING_ACTIVITY,
                impact_scope=NewsAnalysisResult.ImpactScope.EXCHANGE,
                importance=NewsAnalysisResult.Level.LOW,
                rationale=rule.rationale,
                confidence=NewsAnalysisResult.Level.HIGH,
            )
    return None
