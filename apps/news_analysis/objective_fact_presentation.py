from __future__ import annotations

import re


def _normalized_with_positions(value: str) -> tuple[str, list[int]]:
    characters: list[str] = []
    positions: list[int] = []
    for match in re.finditer(r"\S+|\s+", value):
        token = match.group()
        if token.isspace():
            if characters and characters[-1] != " ":
                characters.append(" ")
                positions.append(match.start())
            continue
        for offset, character in enumerate(token):
            characters.append(character)
            positions.append(match.start() + offset)
    if characters and characters[-1] == " ":
        characters.pop()
        positions.pop()
    return "".join(characters), positions


def locate_evidence(value: str, evidence: object) -> dict:
    if not isinstance(evidence, str) or not evidence.strip():
        return {"match_type": "unmatched", "start": None, "end": None}
    exact = value.find(evidence)
    if exact >= 0:
        return {
            "match_type": "exact",
            "start": exact,
            "end": exact + len(evidence),
        }
    normalized_value, positions = _normalized_with_positions(value)
    normalized_evidence = " ".join(evidence.split())
    start = normalized_value.find(normalized_evidence)
    if start < 0 or not normalized_evidence:
        return {"match_type": "unmatched", "start": None, "end": None}
    end_index = start + len(normalized_evidence) - 1
    return {
        "match_type": "whitespace_normalized",
        "start": positions[start],
        "end": positions[end_index] + 1,
    }


def highlighted_segments(value: str, evidences: list[str]) -> list[dict]:
    ranges: list[tuple[int, int]] = []
    for evidence in evidences:
        match = locate_evidence(value, evidence)
        if match["start"] is not None:
            ranges.append((match["start"], match["end"]))
    merged: list[list[int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    if not merged:
        return [{"text": value, "highlight": False}] if value else []
    segments: list[dict] = []
    cursor = 0
    for start, end in merged:
        if cursor < start:
            segments.append({"text": value[cursor:start], "highlight": False})
        segments.append({"text": value[start:end], "highlight": True})
        cursor = end
    if cursor < len(value):
        segments.append({"text": value[cursor:], "highlight": False})
    return segments
