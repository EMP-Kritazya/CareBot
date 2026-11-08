"""Who gets told, and what the messages say. ConsoleNotifier prints instead of sending.

The Twilio notifier (next step) will have the same three methods. Every message is a template
filled from the incident: nothing here is free-form (see PLAN.md, "What CareBot may say").
"""

from __future__ import annotations

import datetime as dt
import threading
import time
from typing import Callable

from carebot.core import state as st
from carebot.escalate.facts import briefing, fact_sheet

YELLOW, DIM, RESET = "\033[33m", "\033[2m", "\033[0m"


def clock(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%-I:%M %p")


def contacted_line(contacted: tuple[str, ...]) -> str:
    """Who has been reached, for the family's message. Only what actually happened."""
    who = [w for w in ("neighbors", "911") if w in contacted]
    if not who:
        return "CareBot is still trying to reach help."
    names = " and ".join("Neighbors" if w == "neighbors" else "911" for w in who)
    return f"{names} {'have' if len(who) > 1 or who == ['neighbors'] else 'has'} been contacted."


def alert_texts(profile: dict, event: str, who: str, ts: float,
                contacted: tuple[str, ...] = ("neighbors", "911")) -> dict[str, str]:
    """The first messages for each audience. Facts only. `contacted`: who was actually reached
    before the family hears about it."""
    name = profile.get("resident_name", "Sandy")
    where = profile.get("address", "their home")
    person = name if who == "resident" else f"someone at {name}'s home"
    what = {"fall": "may have fallen", "stillness": "hasn't moved for a while",
            "manual": "may need help (alert triggered manually)"}.get(event, "may need help")
    base = f"CareBot {clock(ts)}: {person} {what} and didn't answer CareBot's check-in."
    return {
        "neighbors": f"{base} {where}. Reply YES if you can go now, NO if you can't.",
        "family": f"{base} {contacted_line(contacted)} {where}.",
        "911": f"{base} {where}. {profile.get('access_note', '')}".strip(),
    }


def got_up_family_text(profile: dict, incident: st.Incident) -> str:
    """He fell, the camera saw him get back up, then he didn't answer. Family only, facts only."""
    name = profile.get("resident_name", "Sandy")
    person = name if incident.who == "resident" else f"Someone at {name}'s home"
    got_up = clock(incident.got_up_at) if incident.got_up_at else "later"
    return (f"CareBot: {person} fell at {clock(incident.started_at)} and got back up at {got_up} "
            f"(seen by the camera), but didn't answer CareBot's check-in. Nobody else has been contacted. "
            f"You may want to give {name} a call.")


def update_text(profile: dict, status: str, quote: str, source: str, ts: float) -> str:
    name = profile.get("resident_name", "Sandy")
    if source == "camera" and status == "got_up":
        return (f"CareBot update {clock(ts)}: the camera saw {name} get back up. "
                f"No spoken 'I'm OK' from {name} yet. Help has not been called off.")
    if source == "resident":
        return f"CareBot update {clock(ts)}: {name} said: \"{quote}\""
    who = "a reply by text" if source == "sms" else "someone at the device"
    return (f"CareBot update {clock(ts)}: {who} said: \"{quote}\" "
            "The device can't confirm who they are.")


class ConsoleNotifier:
    """Prints what would be sent. Optionally simulates a neighbor replying YES."""

    def __init__(self, profile: dict, post: Callable[[object], None], sim_neighbor_after_s: float | None = None,
                 send_delay_s: float = 1.0) -> None:
        self.profile = profile
        self.post = post
        self.sim_after = sim_neighbor_after_s
        self.delay = send_delay_s
        self.paused = False

    def _print(self, label: str, text: str) -> None:
        print(f"{YELLOW}  -> {label}:{RESET} {text}", flush=True)

    def escalate(self, incident: st.Incident, include_neighbors: bool, family_only: bool = False) -> None:
        if family_only:
            self._print(f"SMS to {self.profile.get('family_name', 'family')} only", got_up_family_text(self.profile, incident))
            threading.Timer(self.delay, lambda: self.post(st.EscalationSent(("family",), time.time()))).start()
            return
        texts = alert_texts(self.profile, incident.event, incident.who, incident.started_at)
        texts["911"] = briefing(fact_sheet(incident, self.profile, time.time()))  # the call's opening statement
        channels = []
        if include_neighbors:
            self._print("SMS to the 3 nearest neighbors", texts["neighbors"])
            channels.append("neighbors")
        self._print(f"SMS + call to {self.profile.get('family_name', 'family')}", texts["family"])
        self._print("call to 911 (stand-in phone)", texts["911"])
        channels += ["family", "911"]

        def sent():
            self.post(st.EscalationSent(tuple(channels), time.time()))
            if include_neighbors and self.sim_after is not None:
                threading.Timer(self.sim_after, self._sim_yes).start()

        threading.Timer(self.delay, sent).start()  # sending takes a moment in real life too

    def _sim_yes(self) -> None:
        if self.paused:
            return
        name = self.profile.get("neighbor_name", "Krit")
        print(f"{DIM}  (simulated) {name} replied YES{RESET}", flush=True)
        self.post(st.ReceiverReply(name.lower(), name, self.profile.get("neighbor_street"), "yes", time.time()))

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def update(self, u: st.SendUpdate) -> None:
        text = update_text(self.profile, u.status, u.quote, u.source, time.time())
        self._print(f"SMS to {self.profile.get('family_name', 'family')} + 911", text)
