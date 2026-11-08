"""Understanding what people say: Claude first, keyword rules as the safety net and the fallback.

Safety rules live in code, not in the prompt:
- if the keyword rules hear "help", it's help, whatever Claude thinks;
- any mention of pain, injury, dizziness or not being able to get up is help (a false alarm costs
  less than a missed one);
- if Claude is off, slow or confused, the keyword result is used.
"""

from __future__ import annotations

import re

from carebot.ai.llm import safe_generate
from carebot.voice.keywords import check_in_intent, report_status_by_keywords

SYMPTOMS = re.compile(r"\b(pain|painful|hurt|hurts|hurting|ache|aching|dizzy|dizziness|faint|fainted|bleed|"
                      r"bleeding|blood|can'?t get up|cannot get up|can'?t move|cannot move|can'?t stand|"
                      r"chest|can'?t breathe|short of breath|weak|numb|fell|fallen|broke|broken)\b")

CHECK_IN_SYSTEM = """CareBot, a home safety robot, just asked an older man "Are you OK?" after he fell or stopped moving.
Classify his spoken answer (a speech-to-text transcript, may contain errors).
- "ok": he clearly says he is fine and needs nothing.
- "help": he asks for help, or mentions pain, injury, dizziness, weakness, or not being able to get up, or sounds distressed.
- "unclear": anything else (mumbling, off-topic, a question, too short to tell).
If he mentions any physical problem, put it in "concern" (a few words); otherwise "".
Return JSON: {"intent": "ok" | "help" | "unclear", "concern": "<string>"}"""

CHECK_IN_SCHEMA = {"type": "object", "properties": {
    "intent": {"type": "string", "enum": ["ok", "help", "unclear"]},
    "concern": {"type": "string"}}, "required": ["intent", "concern"]}

REPORT_SYSTEM = """CareBot, a home safety robot, asked a person who just arrived at a fallen older man's home:
"Who are you, and what's happening?" Classify their answer (speech-to-text, may contain errors).
- "hospital": they say they are taking him to the hospital / ER.
- "ambulance_here": paramedics, EMTs, an ambulance or firefighters are there now.
- "resident_ok": they say he is OK / fine / doesn't need help.
- "with_resident": they are with him helping (neighbor, family, friend), with no outcome yet.
- "unclear": anything else.
Return JSON: {"status": "hospital" | "ambulance_here" | "resident_ok" | "with_resident" | "unclear"}"""

REPORT_SCHEMA = {"type": "object", "properties": {
    "status": {"type": "string", "enum": ["hospital", "ambulance_here", "resident_ok", "with_resident", "unclear"]}},
    "required": ["status"]}


class Understander:
    def __init__(self, ai, enabled: bool = True) -> None:
        self.ai = ai
        self.enabled = enabled and getattr(ai, "enabled", False)
        self.last_source = "keywords"  # "claude" or "keywords", for narration

    def check_in(self, text: str) -> str:
        """ok | help | unclear for an answer to "Are you OK?"."""
        keyword = check_in_intent(text)
        self.last_source = "keywords"
        if keyword == "help" or SYMPTOMS.search(text.lower()):
            return "help"  # safety net: never let a model talk us out of "help"
        if not self.enabled:
            return keyword
        out = safe_generate(self.ai, CHECK_IN_SYSTEM, f"His answer: {text}", CHECK_IN_SCHEMA, max_tokens=1000)
        if not isinstance(out, dict) or out.get("intent") not in ("ok", "help", "unclear"):
            return keyword
        self.last_source = "claude"
        if (out.get("concern") or "").strip():
            return "help"
        return out["intent"]

    def report(self, text: str) -> str:
        """Status for an answer to "Who are you, and what's happening?"."""
        keyword = report_status_by_keywords(text)
        self.last_source = "keywords"
        if not self.enabled:
            return keyword
        out = safe_generate(self.ai, REPORT_SYSTEM, f"Their answer: {text}", REPORT_SCHEMA, max_tokens=1000)
        status = out.get("status") if isinstance(out, dict) else None
        if status not in REPORT_SCHEMA["properties"]["status"]["enum"]:
            return keyword
        self.last_source = "claude"
        if keyword == "hospital" and status != "hospital":
            return keyword  # an explicit "hospital" is never downgraded
        return status
