"""The 911 fact sheet: everything CareBot may tell a dispatcher, built by code from the incident.

Pure. Only observations (what the camera saw, what was heard, who was contacted) and verbatim
quotes. Nothing here is a guess about his condition.
"""

from __future__ import annotations

import datetime as dt

from carebot.core import state as st


def clock(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%-I:%M %p")


EVENT_TEXT = {"fall": "the camera saw a fall", "stillness": "the camera saw no movement for a long time",
              "manual": "an alert was triggered manually from the dashboard"}


def fact_sheet(inc: st.Incident, profile: dict, now: float, camera_now: str | None = None,
               calls_911_back: bool = False) -> dict:
    """camera_now: what the camera sees right now, e.g. "lying on the floor", "out of view".
    calls_911_back: whether CareBot will call 911 again with each update (so it can promise that)."""
    name = profile.get("resident_name", "the resident")
    # Only real "are you OK?" check-ins count as check-ins. Comfort talk ("I'm here with you") and
    # "who are you?" go to `other_prompts`, so nobody is told "3 check-ins" when there were 2.
    check_ins, other, buttons, quotes, neighbors = [], [], [], [], []
    last_ask = "check_in"
    for e in inc.timeline:
        if e.kind == "ask":
            last_ask = e.detail.split(" ")[0]
            target = check_ins if last_ask in ("check_in", "confirm") else other
            what = {"comfort": "CareBot spoke to comfort and listened", "onscene": "CareBot asked who is there"}.get(
                last_ask, f"CareBot asked ({e.detail})")
            target.append(f"{clock(e.ts)}: {what}")
        elif e.kind == "response":
            heard = "no answer" if "silence" in e.detail else f"answer heard: {e.detail}"
            (check_ins if last_ask in ("check_in", "confirm") and "comfort" not in e.detail else other).append(
                f"{clock(e.ts)}: {heard}")
        elif e.kind == "button":
            buttons.append(f"{clock(e.ts)}: button {e.detail}")
        elif e.kind == "receiver":
            neighbors.append(f"{clock(e.ts)}: {e.detail}")
        if e.quote:
            who = name if e.kind == "resident_said" else "someone at the device"
            quotes.append({"time": clock(e.ts), "who": who, "said": e.quote})
    person = name if inc.who == "resident" else f"a person who is not {name} (a visitor)"
    return {
        "address": profile.get("address"),
        "access": profile.get("access_note"),
        "resident": {"name": name, "age": profile.get("resident_age"), "lives_alone": profile.get("lives_alone"),
                     "pronouns": profile.get("resident_pronouns", "they/them")},
        "who_is_involved": person,
        "what_happened": EVENT_TEXT.get(inc.event, inc.event),
        "when": clock(inc.started_at),
        "minutes_since": int((now - inc.started_at) // 60),
        "check_ins": check_ins,
        "other_prompts": other,
        "button_presses": buttons,
        "got_back_up_at": clock(inc.got_up_at) if inc.got_up_at else None,
        "camera_now": camera_now or "unknown",
        "calls_911_back": calls_911_back,
        "contacted": list(inc.channels),
        "family_contact": _family_contact(profile),
        "neighbor_status": inc.neighbor,
        "helper": inc.helper,
        "neighbor_replies": neighbors,
        "quotes": quotes,
        "device_limits": "The device only knows what its camera sees and what its microphone hears. "
                         "It cannot measure breathing, pulse, consciousness or injuries.",
    }


def _family_contact(profile: dict) -> str:
    name, relation = profile.get("family_name"), profile.get("family_relation")
    who = f"{name}, {profile.get('resident_name', 'the resident')}'s {relation}" if name and relation else (name or "family")
    return (f"{who}. CareBot texts and calls them about the incident; replies from the family "
            "don't come back to CareBot, so it doesn't know if they have seen it.") if name else who


def briefing(f: dict) -> str:
    """The opening statement of the 911 call: a template, facts only."""
    r = f["resident"]
    age = f", {r['age']}," if r.get("age") else ""
    alone = " who lives alone" if r.get("lives_alone") else ""
    no_answer = sum(1 for c in f["check_ins"] if "no answer" in c)
    lines = [f"This is CareBot, an automated home safety device, calling about {r['name']}{age}{alone}, "
             f"at {f['address']}.",
             f"At {f['when']}, {f['what_happened']} involving {f['who_is_involved']}."]
    if no_answer:
        lines.append(f"There was no answer to {no_answer} spoken check-in{'s' if no_answer > 1 else ''}.")
    if f["got_back_up_at"]:
        lines.append(f"The camera saw them get back up at {f['got_back_up_at']}.")
    if f["access"]:
        lines.append(f["access"])
    lines.append("You can ask me questions. I can only tell you what the device saw and heard.")
    return " ".join(lines)
