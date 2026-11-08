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


def fact_sheet(inc: st.Incident, profile: dict, now: float, camera_now: str | None = None) -> dict:
    """camera_now: what the camera sees right now, e.g. "lying on the floor", "out of view"."""
    name = profile.get("resident_name", "the resident")
    check_ins, buttons, quotes, neighbors = [], [], [], []
    for e in inc.timeline:
        if e.kind == "ask":
            check_ins.append(f"{clock(e.ts)}: CareBot asked ({e.detail})")
        elif e.kind == "response":
            heard = "no answer" if "silence" in e.detail else f"answer heard: {e.detail}"
            check_ins.append(f"{clock(e.ts)}: {heard}")
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
        "resident": {"name": name, "age": profile.get("resident_age"), "lives_alone": profile.get("lives_alone")},
        "who_is_involved": person,
        "what_happened": EVENT_TEXT.get(inc.event, inc.event),
        "when": clock(inc.started_at),
        "minutes_since": int((now - inc.started_at) // 60),
        "check_ins": check_ins,
        "button_presses": buttons,
        "got_back_up_at": clock(inc.got_up_at) if inc.got_up_at else None,
        "camera_now": camera_now or "unknown",
        "contacted": list(inc.channels),
        "neighbor_status": inc.neighbor,
        "helper": inc.helper,
        "neighbor_replies": neighbors,
        "quotes": quotes,
        "device_limits": "The device only knows what its camera sees and what its microphone hears. "
                         "It cannot measure breathing, pulse, consciousness or injuries.",
    }


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
