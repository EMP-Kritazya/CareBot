"""Turn short speech into a fixed intent. Pure and deterministic.

`check_in_intent` runs on the robot for check-ins (after Vosk). `report_status_by_keywords` is the
offline stand-in for the Claude classifier on "Who are you, and what's happening?" replies.
"""

from __future__ import annotations

import re

_HELP = re.compile(r"\b(help|hurt|can'?t get up|cannot get up|fell|emergency|call 911|ambulance)\b")
_NOT_OK = re.compile(r"\b(not|n't)\s+(ok|okay|fine|alright|all right)\b")
_OK = re.compile(r"\b(i'?m|i am|im)\s+(ok|okay|fine|alright|all right)\b|^\s*(ok|okay|fine|yes|yeah)\b")


def _norm(text: str) -> str:
    return " ".join(text.lower().replace("’", "'").split())


def check_in_intent(text: str) -> str:
    """'help' wins over 'ok' when both appear: a false alarm is cheaper than a missed one."""
    t = _norm(text)
    if not t:
        return "unclear"
    if _HELP.search(t) or _NOT_OK.search(t):
        return "help"
    if _OK.search(t):
        return "ok"
    return "unclear"


_REPORT_RULES = [
    ("hospital", re.compile(r"\b(hospital|emergency room|the er)\b")),
    ("ambulance_here", re.compile(r"\b(ambulance|paramedics?|emts?|firefighters?)\b.*\b(here|arrived|came|is here)\b"
                                  r"|\b(paramedics?|emts?) (are|is) here\b")),
    ("resident_ok", re.compile(r"\b(she'?s|she is|he'?s|he is|they'?re|they are)\s+(ok|okay|fine|alright|all right)\b")),
    ("with_resident", re.compile(r"\b(with her|with him|with them|i'?m here|i am here|neighbor|neighbour|helping)\b")),
]


def report_status_by_keywords(text: str) -> str:
    t = _norm(text)
    for status, pattern in _REPORT_RULES:
        if pattern.search(t):
            return status
    return "unclear"
