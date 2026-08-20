"""Pure candidate matching rules for news event consolidation."""

import re
from datetime import timedelta
from difflib import SequenceMatcher
from typing import Protocol

from .models import ObjectiveFactExtractionResult


class EventMatchingConfig(Protocol):
    publication_window_days: int
    max_candidates_per_input: int
    min_recall_score: float

def result_snapshot(result: ObjectiveFactExtractionResult) -> dict:
    parsed = result.parsed_result if isinstance(result.parsed_result, dict) else {}
    facts = parsed.get("facts") if isinstance(parsed.get("facts"), list) else []
    compact_facts = []
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        compact_facts.append(
            {
                "evidence_text": fact.get("evidence_text"),
                "claim_type": fact.get("claim_type"),
                "amounts": fact.get("amounts") if isinstance(fact.get("amounts"), list) else [],
            }
        )
    return {
        "result_id": result.id,
        "news_id": result.news_record_id,
        "event_title": parsed.get("event_title"),
        "actors": parsed.get("actors") if isinstance(parsed.get("actors"), list) else [],
        "action": parsed.get("action"),
        "object": parsed.get("object") if isinstance(parsed.get("object"), list) else [],
        "event_status": result.event_status or parsed.get("event_status"),
        "facts": compact_facts,
        "objective_summary": result.objective_summary or parsed.get("objective_summary"),
        "information_completeness": result.information_completeness
        or parsed.get("information_completeness"),
        "event_time": parsed.get("event_time"),
        "publication_at": result.news_record.published_at.isoformat(),
    }


def _normalized(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"[^\w\u3400-\u9fff]+", " ", value.casefold()).strip()


def _field_text(snapshot: dict, field: str) -> str:
    value = snapshot.get(field)
    if isinstance(value, list):
        return " ".join(_normalized(item) for item in value if isinstance(item, str))
    return _normalized(value)


GENERIC_TITLE_TOKENS = {
    "about",
    "after",
    "against",
    "announces",
    "announced",
    "bitcoin",
    "blockchain",
    "crypto",
    "cryptocurrency",
    "digital",
    "event",
    "latest",
    "market",
    "markets",
    "million",
    "new",
    "report",
    "reports",
    "says",
    "the",
    "update",
    "updates",
    "with",
}


def _significant_tokens(value: object) -> set[str]:
    normalized = _normalized(value)
    return {
        token
        for token in normalized.split()
        if len(token) >= 3
        and token not in GENERIC_TITLE_TOKENS
        and not token.isdigit()
    }


def _list_values(snapshot: dict, field: str) -> set[str]:
    values = snapshot.get(field)
    if not isinstance(values, list):
        return set()
    return {_normalized(value) for value in values if _normalized(value)}


def _values_overlap(left_values: set[str], right_values: set[str]) -> bool:
    return any(
        left == right
        or left in right
        or right in left
        or SequenceMatcher(None, left, right).ratio() >= 0.86
        for left in left_values
        for right in right_values
    )


def _action_categories(snapshot: dict) -> set[str]:
    action = _field_text(snapshot, "action")
    return {
        category
        for category, terms in ACTION_CATEGORIES.items()
        if any(term in action for term in terms)
    }


def has_candidate_anchor(left: dict, right: dict) -> bool:
    if _stable_ids(left).intersection(_stable_ids(right)):
        return True
    if _values_overlap(_list_values(left, "actors"), _list_values(right, "actors")):
        return True
    if _values_overlap(_list_values(left, "object"), _list_values(right, "object")):
        return True
    if _values_overlap(_list_values(left, "actors"), _list_values(right, "object")):
        return True
    if _values_overlap(_list_values(left, "object"), _list_values(right, "actors")):
        return True
    left_title_tokens = _significant_tokens(left.get("event_title"))
    right_title_tokens = _significant_tokens(right.get("event_title"))
    if left_title_tokens.intersection(right_title_tokens):
        return True
    left_action = _field_text(left, "action")
    right_action = _field_text(right, "action")
    if (
        left_action
        and right_action
        and SequenceMatcher(None, left_action, right_action).ratio() >= 0.82
    ):
        return True
    return bool(_action_categories(left).intersection(_action_categories(right)))


def has_shared_event_anchor(left: dict, right: dict) -> bool:
    if _stable_ids(left).intersection(_stable_ids(right)):
        return True
    if _values_overlap(_list_values(left, "actors"), _list_values(right, "actors")):
        return True
    if _values_overlap(_list_values(left, "object"), _list_values(right, "object")):
        return True
    if _values_overlap(_list_values(left, "actors"), _list_values(right, "object")):
        return True
    if _values_overlap(_list_values(left, "object"), _list_values(right, "actors")):
        return True
    return bool(
        _significant_tokens(left.get("event_title")).intersection(
            _significant_tokens(right.get("event_title"))
        )
    )


def candidate_similarity(left: dict, right: dict) -> float:
    weights = {
        "event_title": 0.30,
        "actors": 0.20,
        "action": 0.20,
        "object": 0.25,
        "event_status": 0.05,
    }
    available = 0.0
    score = 0.0
    for field, weight in weights.items():
        left_text = _field_text(left, field)
        right_text = _field_text(right, field)
        if not left_text or not right_text:
            continue
        available += weight
        score += weight * SequenceMatcher(None, left_text, right_text).ratio()
    return score / available if available else 0.0


def generate_candidate_pairs(
    results: list[ObjectiveFactExtractionResult], config: EventMatchingConfig
) -> list[tuple[ObjectiveFactExtractionResult, ObjectiveFactExtractionResult, float]]:
    snapshots = {result.id: result_snapshot(result) for result in results}
    selected: dict[tuple[int, int], tuple[ObjectiveFactExtractionResult, ObjectiveFactExtractionResult, float]] = {}
    window = timedelta(days=config.publication_window_days)
    for index, right in enumerate(results):
        ranked = []
        for left in results[:index]:
            if right.news_record.published_at - left.news_record.published_at > window:
                continue
            score = candidate_similarity(snapshots[left.id], snapshots[right.id])
            if score < config.min_recall_score or not has_candidate_anchor(
                snapshots[left.id], snapshots[right.id]
            ):
                continue
            ranked.append((score, left.id, left, right))
        ranked.sort(key=lambda item: (-item[0], item[1], item[3].id))
        for score, _, left, candidate_right in ranked[: config.max_candidates_per_input]:
            first, second = sorted((left, candidate_right), key=lambda item: item.id)
            selected[(first.id, second.id)] = (first, second, score)
    return [selected[key] for key in sorted(selected)]


STABLE_ID_PATTERN = re.compile(
    r"\b(?:0x[a-f0-9]{12,}|(?:case|proposal|tx|transaction|docket|hash)[\s:#-]*[a-z0-9-]{4,})\b",
    re.IGNORECASE,
)
ACTION_STAGE_WORDS = {
    "announced",
    "approved",
    "occurred",
    "responded",
    "ruled",
    "settled",
    "proposed",
    "planned",
    "under_investigation",
}
PLANNING_STATUSES = {"announced", "proposed", "planned"}
CONTINUING_EVENT_STATUSES = {"occurred", "ongoing"}
ACTION_CATEGORIES = {
    "responded": ("respond", "回应", "回复"),
    "ruled": ("ruled", "ruling", "裁定", "判决"),
    "settled": ("settled", "settlement", "和解"),
    "sued": ("sued", "sues", "lawsuit", "起诉", "诉讼"),
    "security_incident": (
        "attack",
        "exploit",
        "hack",
        "steal",
        "stole",
        "stolen",
        "theft",
        "sweep",
        "攻击",
        "利用漏洞",
        "盗取",
        "被盗",
    ),
    "fund_transfer": (
        "move funds",
        "moving funds",
        "send funds",
        "sending bitcoin",
        "transfer funds",
        "转移资金",
        "转入交易所",
    ),
    "market_move": (
        "price decline",
        "price drop",
        "price rise",
        "slips under",
        "fell below",
        "falls below",
        "rallied",
        "surged",
        "价格下跌",
        "价格上涨",
        "跌破",
        "上涨",
    ),
    "listed": ("list", "listing", "上线", "上币"),
    "approved": ("approve", "approved", "批准", "获批"),
}


def _stable_ids(snapshot: dict) -> set[str]:
    text = " ".join(
        [
            _field_text(snapshot, "event_title"),
            _field_text(snapshot, "action"),
            _field_text(snapshot, "object"),
            str(snapshot.get("objective_summary") or ""),
        ]
    )
    return {_normalized(match.group(0)) for match in STABLE_ID_PATTERN.finditer(text)}


def hard_rejection_reason(left: dict, right: dict) -> str | None:
    left_status = _normalized(left.get("event_status"))
    right_status = _normalized(right.get("event_status"))
    if (
        left_status
        and right_status
        and left_status != "unknown"
        and right_status != "unknown"
        and left_status != right_status
        and left_status in ACTION_STAGE_WORDS
        and right_status in ACTION_STAGE_WORDS
        and not {left_status, right_status}.issubset(PLANNING_STATUSES)
        and not {left_status, right_status}.issubset(CONTINUING_EVENT_STATUSES)
        and has_shared_event_anchor(left, right)
    ):
        return f"明确属于不同事件阶段：{left_status} / {right_status}。"

    left_ids = _stable_ids(left)
    right_ids = _stable_ids(right)
    if left_ids and right_ids and left_ids.isdisjoint(right_ids):
        return "存在明确互斥的稳定标识。"

    left_action = _field_text(left, "action")
    right_action = _field_text(right, "action")
    action_similarity = (
        SequenceMatcher(None, left_action, right_action).ratio()
        if left_action and right_action
        else 0.0
    )
    left_objects = {_normalized(value) for value in left.get("object", []) if _normalized(value)}
    right_objects = {_normalized(value) for value in right.get("object", []) if _normalized(value)}
    objects_overlap = any(
        left_value in right_value or right_value in left_value
        for left_value in left_objects
        for right_value in right_objects
    )
    if (
        left_objects
        and right_objects
        and left_objects.isdisjoint(right_objects)
        and not objects_overlap
        and action_similarity >= 0.72
    ):
        return "主要动作相近，但明确动作对象不同。"

    left_categories = _action_categories(left)
    right_categories = _action_categories(right)
    if (
        left_categories
        and right_categories
        and left_categories.isdisjoint(right_categories)
        and has_shared_event_anchor(left, right)
    ):
        return "明确描述了不同的现实动作或决定。"
    if (
        left_action
        and right_action
        and action_similarity < 0.42
        and not left_categories.intersection(right_categories)
        and has_shared_event_anchor(left, right)
    ):
        left_words = set(left_action.split())
        right_words = set(right_action.split())
        if left_words and right_words and left_words.isdisjoint(right_words):
            return "明确描述了不同的现实动作。"
    return None


