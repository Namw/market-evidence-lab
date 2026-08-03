from __future__ import annotations


EVENT_STATUS_VALUES = (
    "occurred",
    "approved",
    "announced",
    "proposed",
    "planned",
    "ongoing",
    "under_investigation",
    "unconfirmed",
    "unknown",
)
EVENT_STATUS_CHOICES = tuple((value, value) for value in EVENT_STATUS_VALUES)
EVENT_STATUS_PROMPT_TEXT = "、".join(EVENT_STATUS_VALUES)
