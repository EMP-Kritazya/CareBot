"""Drive the real state machine through a situation, step by step, with a fake clock.

Used by the story tests and `apps/simulate.py`. Speech goes through the same keyword rules the
robot uses, so a story exercises the real decision code end to end (minus hardware and Twilio).
"""

from __future__ import annotations

from typing import Callable

from carebot.core import state as st
from carebot.core.script import Profile
from carebot.voice.keywords import check_in_intent, report_status_by_keywords


class Scenario:
    def __init__(self, profile: Profile | None = None, on_actions: Callable[[float, list], None] | None = None):
        self.machine = st.Machine(profile)
        self.state = st.Robot()
        self.t = 0.0
        self.actions: list = []
        self._on_actions = on_actions

    # ---- plumbing

    def send(self, event) -> list:
        self.state, out = self.machine.step(self.state, event)
        self.actions.extend(out)
        if self._on_actions:
            self._on_actions(self.t, out)
        return out

    def wait(self, seconds: float) -> "Scenario":
        self.t += seconds
        return self

    @property
    def mode(self) -> str:
        return self.state.mode

    @property
    def incident(self) -> st.Incident | None:
        return self.state.incident or (self.state.closed[-1] if self.state.closed else None)

    def last(self, kind: type):
        for a in reversed(self.actions):
            if isinstance(a, kind):
                return a
        return None

    def of(self, kind: type) -> list:
        return [a for a in self.actions if isinstance(a, kind)]

    @property
    def said(self) -> list[str]:
        out = []
        for a in self.actions:
            if isinstance(a, st.Say):
                out.append(a.text)
            elif isinstance(a, st.Ask):
                out.append(a.text)
        return out

    @property
    def outbound(self) -> list:
        """Anything that leaves the house: escalations and updates (SMS, calls, 911)."""
        return [a for a in self.actions if isinstance(a, (st.Escalate, st.SendUpdate))]

    def _ask_id(self) -> int:
        ask = self.last(st.Ask)
        return ask.ask_id if ask else 0

    # ---- things that happen in the room

    def fall(self, who: str = "resident"):
        return self.send(st.Detected("fall", who, self.t))

    def stillness(self):
        return self.send(st.Detected("stillness", "resident", self.t))

    def trigger(self):
        return self.send(st.ManualTrigger(self.t))

    def speak(self, text: str):
        """Someone answers the robot's latest question out loud."""
        ask = self.last(st.Ask)
        if ask and ask.listen == "transcript":
            return self.send(st.Reported(report_status_by_keywords(text), text, "in_room", self.t, ask.ask_id))
        return self.send(st.Heard(self._ask_id(), check_in_intent(text), text, self.t))

    def silence(self, seconds: float = 15):
        self.wait(seconds)
        return self.send(st.Silence(self._ask_id(), self.t))

    def press(self, resident_posture: str = "upright"):
        return self.send(st.ButtonPressed(resident_posture, self.t))

    # ---- things that happen outside

    def sent(self, channels=("neighbors", "family", "911")):
        return self.send(st.EscalationSent(tuple(channels), self.t))

    def neighbor(self, name: str, reply: str = "yes", street: str | None = None, rid: str | None = None):
        return self.send(st.ReceiverReply(rid or name.lower(), name, street, reply, self.t))

    def sms(self, text: str):
        return self.send(st.Reported(report_status_by_keywords(text), text, "sms", self.t))

    def call(self, target: str, status: str):
        return self.send(st.CallUpdate(target, status, self.t))

    def reset(self):
        return self.send(st.Reset(self.t))
