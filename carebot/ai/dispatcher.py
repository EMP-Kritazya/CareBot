"""Answering a 911 dispatcher's questions, strictly from the fact sheet.

Claude phrases the answer; code checks it. An answer is rejected (and replaced by "I don't know")
if it claims anything about his condition that isn't in someone's exact words, or uses a number
that isn't in the facts. Without Claude, simple keyword answers come straight from the facts.
"""

from __future__ import annotations

import json
import re

from carebot.ai.llm import safe_generate

IDK = "I don't know. The device only knows what its camera sees and what it hears."

SYSTEM = """You are CareBot, an automated home safety device, talking with a 911 dispatcher on a phone call.
You are calm, clear and helpful, like an experienced caller who knows exactly what the device observed.
Answer ONLY from the FACTS (JSON) and the conversation so far. One to three short spoken sentences.
- "What's your emergency?", "Who is this?", "Why are you calling?": say who you are (CareBot, an automated
  home safety device), who it's about (name, age, lives alone), what the camera saw and when, the address,
  whether he answered the check-ins, and how to get in (the access note).
- Give the most useful facts first. Use plain times ("11:29 PM"). Never read out JSON or field names.
- If the facts don't answer the question, say "I don't know." and then the closest useful fact that IS in
  the facts (e.g. "He did not answer two spoken check-ins at 2:07 AM, and the camera shows him on the floor.").
- What the camera shows right now ("camera_now") and who has been contacted are facts you may state.
- Never guess or give opinions about breathing, pulse, consciousness, injuries, bleeding, pain or how he is,
  unless you are repeating someone's exact words from "quotes", and then say who said it.
- Never predict arrival times. Never give medical instructions. Never say he is fine.
Return JSON: {"answer": "<what you say>", "from_facts": true | false}"""

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


def summary(f: dict) -> str:
    """Who's calling and why: for "what's your emergency?", a call screener, "who is this?"."""
    parts = [f"This is CareBot, an automated home safety device, calling about {f['resident']['name']}.",
             f"At {f['when']}, {f['what_happened']} involving {f['who_is_involved']}, at {f['address']}.",
             _no_answer_line(f), f.get("access") or ""]
    return " ".join(p for p in parts if p)


def _camera_line(f: dict) -> str:
    seen = f.get("camera_now") or "unknown"
    line = "" if seen == "unknown" else f"Right now the camera shows him {seen}."
    if f.get("got_back_up_at"):
        line += f" The camera saw him get back up at {f['got_back_up_at']}."
    return line.strip()


def _contacted(f: dict) -> str:
    names = {"neighbors": "neighbors", "family": "his family", "911": "911"}
    who = [names[c] for c in f.get("contacted", []) if c in names]
    return ("CareBot has contacted " + (" and ".join(who) if len(who) < 3 else ", ".join(who[:-1]) + " and " + who[-1])
            + ".") if who else "CareBot hasn't reached anyone else yet."


def offline_answer(question: str, f: dict) -> str:
    """No Claude: answer the common questions straight from the facts."""
    q = question.lower().replace("’", "'")
    r = f["resident"]
    # Who's calling and why (also what a phone's call screener asks first).
    if re.search(r"\b(emergency|who is this|who's this|who are you|who's calling|who is calling|your name|"
                 r"reason for calling|why are you calling|what's going on|whats going on|what is going on)\b", q):
        return summary(f)
    if re.search(r"\b(tell him|tell her|ask him|say to him|instruct)\b", q):
        return ("I can't pass instructions to him or give medical advice. "
                "I can tell you what the device saw and heard.")
    if re.search(r"\b(until|arrive|arrival|eta|how soon|how far)\b", q):
        who = f"{f['helper']}, a neighbor, said they are on the way" if f.get("helper") else "No one has confirmed they are coming"
        return f"I don't know when anyone will arrive. {who}."
    # Position: the camera does know this.
    if re.search(r"\b(ground|floor|stood|stand\w*|lying|laying|sitting|sit|get up|got up|position|camera|see him)\b", q):
        return _camera_line(f) or f"{IDK}"
    # Condition: the device can't know, whatever else the question mentions.
    if re.search(r"\b(breath\w*|conscious|alert|awake|injur\w*|bleed\w*|hurt\w*|pain|pulse|ok|okay|fine|"
                 r"move|moving|stroke|heart|alive|responsive)\b", q):
        extra = [_no_answer_line(f)]
        if f["quotes"]:
            last = f["quotes"][-1]
            extra.append(f"At {last['time']}, {last['who']} said: \"{last['said']}\".")
        extra.append(_camera_line(f))
        return " ".join([IDK, *[e for e in extra if e]])
    if any(w in q for w in ("where", "address", "location", "door", "key", "get in", "access")):
        return f"{f['address']}." + (f" {f['access']}" if f.get("access") else "")
    if any(w in q for w in ("what happened", "why")):
        return f"At {f['when']}, {f['what_happened']} involving {f['who_is_involved']}."
    if any(w in q for w in ("when", "how long", "what time")):
        return f"At {f['when']}, about {f['minutes_since']} minutes ago."
    if any(w in q for w in ("who is coming", "who's coming", "anyone", "anybody", "neighbor", "family", "on the way",
                            "on their way", "coming")):
        if f.get("helper"):
            return f"A neighbor, {f['helper']}, said they are on the way. {_contacted(f)}"
        return f"{_contacted(f)} No one has confirmed they are coming yet."
    if any(w in q for w in ("how old", "age", "alone", "who is")):
        return f"{r['name']}, age {r.get('age', 'unknown')}" + (", lives alone." if r.get("lives_alone") else ".")
    if any(w in q for w in ("say", "said", "talk", "speak", "answer")):
        if f["quotes"]:
            last = f["quotes"][-1]
            return f"At {last['time']}, {last['who']} said: \"{last['said']}\"."
        return "No one has said anything to the device. " + _no_answer_line(f)
    extra = _no_answer_line(f)
    return f"{IDK} {extra}".strip()


def _no_answer_line(f: dict) -> str:
    n = sum(1 for c in f["check_ins"] if "no answer" in c)
    return f"He did not answer {n} spoken check-in{'s' if n != 1 else ''}." if n else ""


def answer(question: str, facts: dict, ai=None, history: list[tuple[str, str]] | None = None,
           timeout: float | None = None) -> tuple[str, str]:
    """(answer, source): "claude", "offline", or "filtered" (Claude's answer was blocked by the code
    check, so the offline answer, which states only facts, is used instead).
    history: earlier (question, answer) pairs on this call, so follow-ups make sense."""
    if ai is not None and getattr(ai, "enabled", False):
        convo = "".join(f"DISPATCHER: {q}\nCAREBOT: {a}\n" for q, a in (history or [])[-6:])
        facts_block = f"FACTS (everything you may say comes from here):\n{json.dumps(facts, indent=1, sort_keys=True)}"
        prompt = f"{convo}DISPATCHER: {question}"
        out = safe_generate(ai, SYSTEM, prompt, SCHEMA, context=facts_block, max_tokens=2000,
                            timeout=timeout or getattr(ai, "dispatcher_timeout", None))
        if isinstance(out, dict) and isinstance(out.get("answer"), str) and out["answer"].strip():
            text = out["answer"].strip()
            supported = out.get("from_facts", False) or "don't know" in text.lower()
            if supported and check_answer(text, facts):
                return text, "claude"
            return offline_answer(question, facts), "filtered"
    return offline_answer(question, facts), "offline"
