"""Warmer speech: Gemini rewrites CareBot's template lines, and code checks every rewrite.

The state machine picks the line (what may be said). Gemini only changes how it sounds. A rewrite
is spoken only if `check_line` passes: same names, same cues, no new numbers, no new promises, no
medical words, no false comfort. Otherwise the template is spoken, word for word.
"""

from __future__ import annotations

import re
import threading

from carebot.ai.gemini import safe_generate

SYSTEM = """You rewrite lines for CareBot, a small home robot that checks on an older man who lives alone.
Make the line sound like a calm, kind person speaking slowly and clearly to an 81-year-old.
Strict rules:
- Keep every fact in the line. Add no new facts: no times, no numbers, no names, no new people.
- Never say help is coming, on the way, or has been called unless the original line says so.
- Never give medical advice or opinions (no "don't move", "stay still", "you might be hurt", etc.).
- No false comfort ("don't worry", "you'll be fine", "everything will be OK").
- Keep these exact phrases if the original has them: "I'm OK", 'help', "press my button".
- One to three short sentences, at most 30 words. Plain spoken English, no emojis, no quotes around the line.
Return JSON: {"line": "<the rewritten line>"}"""

REPLY_SYSTEM = SYSTEM + """
The person just said something to CareBot. You may briefly acknowledge what they said with
kindness (for example "I'm sorry your hip hurts"), but never interpret it medically and never
promise anything beyond the original line."""

SCHEMA = {"type": "object", "properties": {"line": {"type": "string"}}, "required": ["line"]}

MEDICAL = ("injur", "fractur", "broken", "bleed", "breath", "conscious", "concuss", "don't move", "do not move",
           "stay still", "keep still", "lie down", "sit up", "medication", "medicine", "pills", "doctor",
           "ambulance", "paramedic", "diagnos", "stroke", "heart attack", "not to move", "stay put",
           "stay where you are", "you should", "make sure", "try to", "careful")
FALSE_COMFORT = ("don't worry", "do not worry", "you'll be fine", "you will be fine", "you'll be ok",
                 "you will be ok", "everything will be", "nothing to worry", "you're fine", "you are fine",
                 "no need to worry")
ARRIVAL = ("on the way", "on his way", "on her way", "on their way", "coming", "arriving", "be there", "be here soon")
SEND_CLAIMS = ("called", "texted", "contacted", "notified", "alerted", "messaged", "told")
CUES = ("i'm ok", "help", "press my button")


def _norm(text: str) -> str:
    return " ".join(text.lower().replace("’", "'").replace("‘", "'").split())


def check_line(template: str, rewrite: str, names: tuple[str, ...] = (), heard: str = "") -> bool:
    """Pure. True if `rewrite` may be spoken in place of `template`.
    `names`: people CareBot may name (resident, family, neighbors). `heard`: what the person just
    said, for replies; words from it may be echoed back."""
    if not rewrite or not rewrite.strip():
        return False
    t, r, h = _norm(template), _norm(rewrite), _norm(heard)
    if len(r.split()) > max(30, 2 * len(t.split()) + 8):
        return False
    for name in (*names, "911"):
        n = name.lower()
        if (n in t) != (n in r):  # every name kept, none added
            return False
    for cue in CUES:
        if cue in t and cue not in r:
            return False
    if set(re.findall(r"\d+", r)) - set(re.findall(r"\d+", t)):
        return False  # no new numbers (times, ETAs, counts)
    if "minute" in r and "minute" not in t:
        return False
    for group in (ARRIVAL, SEND_CLAIMS):
        if any(w in r for w in group) and not any(w in t for w in group):
            return False  # no new promises of help or claims of who was contacted
    for w in MEDICAL:
        if w in r and w not in t and w not in h:
            return False
    if any(w in r for w in FALSE_COMFORT):
        return False
    return True


class Phraser:
    """Rewrites lines with Gemini, checks them, caches the good ones."""

    def __init__(self, ai, names: tuple[str, ...], enabled: bool = True) -> None:
        self.ai = ai
        self.names = names
        self.enabled = enabled and getattr(ai, "enabled", False)
        self.cache: dict[str, str] = {}
        self.rejected = 0
        self._lock = threading.Lock()

    def polish(self, text: str, heard: str = "") -> tuple[str, bool]:
        """(line to speak, True if Gemini's rewrite is used)."""
        if not self.enabled or not text:
            return text, False
        if not heard:
            with self._lock:
                if text in self.cache:
                    return self.cache[text], True
        prompt = f"Original line: {text}"
        if heard:
            prompt = f"The person said: {heard}\n{prompt}"
        out = safe_generate(self.ai, REPLY_SYSTEM if heard else SYSTEM, prompt, SCHEMA, temperature=0.6)
        line = (out or {}).get("line", "").strip().strip('"') if isinstance(out, dict) else ""
        if not check_line(text, line, self.names, heard):
            if line:
                self.rejected += 1
            return text, False
        if not heard:
            with self._lock:
                self.cache[text] = line
        return line, True

    def prewarm(self, lines: list[str]) -> None:
        """Polish fixed lines in the background, so the first check-in after a fall costs nothing."""
        if not self.enabled:
            return

        def run():
            for line in lines:
                self.polish(line)

        threading.Thread(target=run, daemon=True, name="prewarm").start()


def fixed_lines(profile, neighbor: str | None = None) -> list[str]:
    """Every line CareBot can say that doesn't depend on live input, for prewarming."""
    from carebot.core import script as sc

    lines = []
    for who in ("resident", "other"):
        for n in (1, 2):
            for event in ("fall", "stillness", "manual"):
                lines.append(sc.check_in(profile, event, who, n))
            lines += [sc.got_up_check_in(profile, who, n), sc.confirm(profile, who, n)]
            for posture in ("lying", "out_of_view", "upright"):
                lines.append(sc.onscene(profile, who, posture, n))
        lines += [sc.glad_ok(profile, who), sc.told_family_got_up(profile, who),
                  sc.comfort(profile, who, None, True), sc.comfort(profile, who, None, False)]
        if neighbor:
            lines.append(sc.comfort(profile, who, neighbor, True))
    lines += [sc.getting_help(profile, ("neighbors", "family", "911")), sc.passed_on(profile),
              sc.still_getting_help(), sc.psap_unanswered()]
    if neighbor:
        lines.append(sc.helper_coming(neighbor, None))
    return list(dict.fromkeys(lines))
