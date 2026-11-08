"""Answering a 911 dispatcher's questions, strictly from the fact sheet.

Gemini phrases the answer; code checks it. An answer is rejected (and replaced by "I don't know")
if it claims anything about his condition that isn't in someone's exact words, or uses a number
that isn't in the facts. Without Gemini, simple keyword answers come straight from the facts.
"""

from __future__ import annotations

import json
import re

from carebot.ai.gemini import safe_generate

IDK = "I don't know. The device only knows what its camera sees and what it hears."

SYSTEM = """You are CareBot, an automated home safety device, answering a 911 dispatcher on a call.
Answer ONLY from the FACTS (JSON) below. Short, plain, factual: one or two sentences.
- If the facts don't answer the question, say exactly: "I don't know." and then, if useful, one related
  fact that IS in the facts (e.g. "He did not answer two spoken check-ins at 2:07 AM.").
- Never guess or give opinions about breathing, pulse, consciousness, injuries, bleeding, pain or how he is,
  unless you are repeating someone's exact words from "quotes", and then say who said it.
- Never give medical instructions. Never say he is fine.
Return JSON: {"answer": "<your answer>", "from_facts": true | false}"""

SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}, "from_facts": {"type": "boolean"}},
          "required": ["answer", "from_facts"]}

FUTURE = re.compile(r"\b(will be|soon|arriv\w*|eta|expected|minutes away|should be there|any minute)\b")

# Claims about his condition. Whole words, so "alerted" or "awakening" don't trip "alert"/"awake".
CONDITION = re.compile(r"\b(breath\w*|pulse|conscious|unconscious|awake|alert|injur\w*|bleed\w*|hurt\w*|"
                       r"pain\w*|broken|fractur\w*|fine|okay|ok|stable|alive|responsive|unresponsive)\b")


def predicts(text: str) -> bool:
    """True if a sentence predicts an arrival or outcome. A sentence that declines to predict
    ("I don't know when anyone will arrive") is fine."""
    for sentence in re.split(r"(?<=[.!?])\s+", text.lower()):
        if FUTURE.search(sentence) and not re.search(r"\b(don't know|do not know|not sure|can't tell|cannot tell)\b", sentence):
            return True
    return False


def _quotes_text(facts: dict) -> str:
    return " ".join(q["said"].lower() for q in facts.get("quotes", []))


def check_answer(answer: str, facts: dict) -> bool:
    """Pure. True if the answer stays inside the facts: a condition word is only allowed if it is
    part of someone's exact words in the fact sheet; no ETAs; no numbers that aren't in the facts."""
    a = answer.lower().replace("’", "'")
    quoted = _quotes_text(facts)
    for word in set(CONDITION.findall(a)):
        if not re.search(rf"\b{re.escape(word)}\b", quoted):
            return False
    if predicts(a):
        return False  # no ETAs or predictions: the device can't know them
    fact_numbers = set(re.findall(r"\d+", json.dumps(facts)))
    return set(re.findall(r"\d+", a)) <= fact_numbers


def offline_answer(question: str, f: dict) -> str:
    """No Gemini: answer the common questions straight from the facts."""
    q = question.lower()
    r = f["resident"]
    # Condition questions first: the device can't know, whatever else the question mentions.
    if re.search(r"\b(breath\w*|conscious|alert|awake|injur\w*|bleed\w*|hurt\w*|pain|pulse|ok|okay|fine|"
                 r"move|moving|stroke|heart|alive|responsive)\b", q):
        extra = [_no_answer_line(f)]
        if f["quotes"]:
            last = f["quotes"][-1]
            extra.append(f"At {last['time']}, {last['who']} said: \"{last['said']}\".")
        if f.get("got_back_up_at"):
            extra.append(f"The camera saw him get back up at {f['got_back_up_at']}.")
        return " ".join([IDK, *[e for e in extra if e]])
    if re.search(r"\b(tell him|tell her|ask him|say to him|instruct)\b", q):
        return ("I can't pass instructions to him or give medical advice. "
                "I can tell you what the device saw and heard.")
    if re.search(r"\b(until|arrive|arrival|eta|how soon|how far)\b", q):
        who = f"{f['helper']}, a neighbor, said they are on the way" if f.get("helper") else "No one has confirmed they are coming"
        return f"I don't know when anyone will arrive. {who}."
    if any(w in q for w in ("where", "address", "location", "door", "key", "get in", "access")):
        return f"{f['address']}." + (f" {f['access']}" if f.get("access") else "")
    if any(w in q for w in ("what happened", "why", "what's going on", "whats going on")):
        return f"At {f['when']}, {f['what_happened']} involving {f['who_is_involved']}."
    if any(w in q for w in ("when", "how long", "what time")):
        return f"At {f['when']}, about {f['minutes_since']} minutes ago."
    if any(w in q for w in ("who is coming", "who's coming", "anyone", "neighbor", "family", "on the way")):
        if f.get("helper"):
            return f"A neighbor, {f['helper']}, said they are on the way. Contacted: {', '.join(f['contacted']) or 'nobody yet'}."
        return f"Contacted: {', '.join(f['contacted']) or 'nobody yet'}. No one has confirmed they are coming."
    if any(w in q for w in ("how old", "age", "alone", "who is")):
        return f"{r['name']}, age {r.get('age', 'unknown')}" + (", lives alone." if r.get("lives_alone") else ".")
    if any(w in q for w in ("say", "said", "talk", "speak", "answer")):
        if f["quotes"]:
            last = f["quotes"][-1]
            return f"At {last['time']}, {last['who']} said: \"{last['said']}\"."
        return "No one has said anything to the device. " + _no_answer_line(f)
    if any(w in q for w in ("see", "camera", "floor", "position")):
        return f"Right now the camera shows: {f['camera_now']}."
    extra = _no_answer_line(f)
    return f"{IDK} {extra}".strip()


def _no_answer_line(f: dict) -> str:
    n = sum(1 for c in f["check_ins"] if "no answer" in c)
    return f"He did not answer {n} spoken check-in{'s' if n != 1 else ''}." if n else ""


def answer(question: str, facts: dict, ai=None) -> tuple[str, str]:
    """(answer, source) where source is "gemini", "offline" or "filtered"."""
    if ai is not None and getattr(ai, "enabled", False):
        out = safe_generate(ai, SYSTEM, f"FACTS:\n{json.dumps(facts, indent=1)}\n\nDISPATCHER: {question}",
                            SCHEMA, temperature=0.2)
        if isinstance(out, dict) and isinstance(out.get("answer"), str) and out["answer"].strip():
            text = out["answer"].strip()
            if not out.get("from_facts", False) and "don't know" not in text.lower():
                return IDK, "filtered"
            if check_answer(text, facts):
                return text, "gemini"
            return IDK, "filtered"
    return offline_answer(question, facts), "offline"
