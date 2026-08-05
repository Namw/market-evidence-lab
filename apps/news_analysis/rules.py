from __future__ import annotations

import re
from dataclasses import dataclass

from apps.news_data.models import NewsRawRecord
from apps.news_data.sources import (
    BINANCE_ANNOUNCEMENTS_CODE,
    BLS_CODE,
    FEDERAL_RESERVE_CODE,
)

from .models import NewsAnalysisResult


@dataclass(frozen=True, slots=True)
class RuleDecision:
    rule_id: str
    conclusion: str
    rationale: str


@dataclass(frozen=True, slots=True)
class TitleRule:
    rule_id: str
    pattern: re.Pattern[str]
    conclusion: str
    rationale: str


BINANCE_TITLE_RULES = (
    TitleRule(
        "binance_marketing_quiz_v2",
        re.compile(r"\b(?:quiz|word of the day|wotd|learn\s*(?:&|and)\s*earn)\b", re.I),
        NewsAnalysisResult.Conclusion.IRRELEVANT,
        "标题明确属于答题或学习奖励活动，与 ETH 方向判断无关。",
    ),
    TitleRule(
        "binance_marketing_referral_v2",
        re.compile(
            r"\b(?:referral\s+(?:campaign|program|promotion)|refer\s+(?:friends?|users?).{0,35}\bearn)\b",
            re.I,
        ),
        NewsAnalysisResult.Conclusion.IRRELEVANT,
        "标题明确属于推荐返佣或拉新奖励活动，与 ETH 方向判断无关。",
    ),
    TitleRule(
        "binance_marketing_competition_v2",
        re.compile(r"\b(?:trading\s+(?:competition|tournament)|trade.{0,30}\bwin\b)\b", re.I),
        NewsAnalysisResult.Conclusion.IRRELEVANT,
        "标题明确属于交易竞赛，与 ETH 方向判断无关。",
    ),
    TitleRule(
        "binance_marketing_rewards_v2",
        re.compile(
            r"\b(?:giveaway|lucky\s+draw|complete\s+(?:the\s+)?tasks?.{0,40}(?:reward|prize)|share\s+.{0,25}\brewards?\b)\b",
            re.I,
        ),
        NewsAnalysisResult.Conclusion.IRRELEVANT,
        "标题明确属于抽奖、奖励任务或奖品活动，与 ETH 方向判断无关。",
    ),
)

ETH_TITLE_RULES = (
    TitleRule(
        "eth_spot_etf_approved_v1",
        re.compile(
            r"\b(?:approve[ds]?|approval)\b.{0,60}\b(?:spot\s+)?(?:ethereum|ether|eth)\b.{0,25}\betf\b|"
            r"\b(?:spot\s+)?(?:ethereum|ether|eth)\b.{0,25}\betf\b.{0,60}\b(?:approve[ds]?|approval)\b",
            re.I,
        ),
        NewsAnalysisResult.Conclusion.BULLISH,
        "标题明确表示 ETH 现货 ETF 获批，增加合规投资入口，判定为利好。",
    ),
    TitleRule(
        "eth_spot_etf_rejected_v1",
        re.compile(
            r"\b(?:reject(?:ed|s|ion)?|deny|denied)\b.{0,60}\b(?:spot\s+)?(?:ethereum|ether|eth)\b.{0,25}\betf\b|"
            r"\b(?:spot\s+)?(?:ethereum|ether|eth)\b.{0,25}\betf\b.{0,60}\b(?:reject(?:ed|s|ion)?|deny|denied)\b",
            re.I,
        ),
        NewsAnalysisResult.Conclusion.BEARISH,
        "标题明确表示 ETH 现货 ETF 被拒，减少合规投资入口，判定为利空。",
    ),
)

OFFICIAL_MACRO_SOURCE_CODES = {FEDERAL_RESERVE_CODE, BLS_CODE}


def match_fixed_rule(record: NewsRawRecord) -> RuleDecision | None:
    """Return only high-certainty conclusions available from the title alone."""
    if record.source.code in OFFICIAL_MACRO_SOURCE_CODES:
        return RuleDecision(
            rule_id="official_macro_release_unclear_v1",
            conclusion=NewsAnalysisResult.Conclusion.UNCLEAR,
            rationale=(
                "该条目属于已选定的官方宏观发布，保留为系统性市场事件候选；"
                "仅凭发布内容不预设其对 ETH 的方向。"
            ),
        )
    title = " ".join(record.title.split())
    rules = ETH_TITLE_RULES
    if record.source.code == BINANCE_ANNOUNCEMENTS_CODE:
        rules = (*BINANCE_TITLE_RULES, *rules)
    for rule in rules:
        if rule.pattern.search(title):
            return RuleDecision(
                rule_id=rule.rule_id,
                conclusion=rule.conclusion,
                rationale=rule.rationale,
            )
    return None
