"""CareBot's state machine. Pure: no I/O, no clocks, no randomness.

    new_state, actions = machine.step(state, event)

Events come from vision, voice, the button, Twilio and the map. Actions are executed by the
runtime (speak, beep, escalate, publish). Every rule in PLAN.md that decides what happens next or
what may be said lives here or in script.py, so it can be unit tested.

Modes:
    MONITORING --fall seen and not up | resident stillness | map trigger--> CHECK_IN
    CHECK_IN   --"I'm OK" (voice, optionally after the button)----------> MONITORING
    CHECK_IN   --"help" | 2 silent asks | button then 2 silent asks------> ALERTED
    ALERTED    --neighbor YES / reports / button------------------------> ... --> MONITORING
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal, Union

from carebot.core import script
from carebot.core.script import Profile

Mode = Literal["MONITORING", "CHECK_IN", "ALERTED"]
EventKind = Literal["fall", "stillness", "manual"]
Who = Literal["resident", "other"]
Posture = Literal["upright", "sitting", "lying", "out_of_view"]
ReportStatus = Literal["hospital", "ambulance_here", "with_her", "she_is_ok", "unclear"]
Awaiting = Literal["check_in", "confirm", "onscene"]

CLOSING_REPORTS = ("hospital", "ambulance_here", "she_is_ok")


# ---------------------------------------------------------------- events (inputs)

@dataclass(frozen=True)
class Detected:
    """Vision confirmed a fall (seen, and not back up) or resident stillness in view."""
    kind: Literal["fall", "stillness"]
    who: Who
    ts: float


@dataclass(frozen=True)
class ManualTrigger:
    """The map's Trigger alert button. Runs the exact fall pipeline."""
    ts: float


@dataclass(frozen=True)
class Heard:
    """Speech after an Ask, sorted by keywords into ok / help / unclear."""
    ask_id: int
    intent: Literal["ok", "help", "unclear"]
    text: str
    ts: float


@dataclass(frozen=True)
class Silence:
    """No speech within the listen window after an Ask."""
    ask_id: int
    ts: float


@dataclass(frozen=True)
class ButtonPressed:
    """The robot's button. `resident_posture` is what the camera saw at the press."""
    resident_posture: Posture
    ts: float


@dataclass(frozen=True)
class Reported:
    """A person's words, sorted into a status. In the room (after the onscene ask) or by SMS."""
    status: ReportStatus
    quote: str
    source: Literal["in_room", "sms"]
    ts: float
    ask_id: int | None = None  # set for in-room replies to an onscene Ask


@dataclass(frozen=True)
class EscalationSent:
    """The runtime finished the first sends. `channels` lists only the ones that succeeded."""
    channels: tuple[str, ...]  # subset of ("neighbors", "family", "911")
    ts: float


@dataclass(frozen=True)
class ReceiverReply:
    receiver_id: str
    name: str
    street: str | None
    reply: Literal["yes", "no"]
    ts: float


@dataclass(frozen=True)
class CallUpdate:
    target: Literal["family", "911"]
    status: Literal["answered", "unanswered"]
    ts: float


@dataclass(frozen=True)
class Reset:
    ts: float


Event = Union[Detected, ManualTrigger, Heard, Silence, ButtonPressed, Reported,
              EscalationSent, ReceiverReply, CallUpdate, Reset]


# ---------------------------------------------------------------- actions (outputs)

@dataclass(frozen=True)
class SetEyes:
    state: str


@dataclass(frozen=True)
class Chirp:
    pass


@dataclass(frozen=True)
class StartAlarm:
    pass


@dataclass(frozen=True)
class StopAlarm:
    pass


@dataclass(frozen=True)
class Say:
    text: str


@dataclass(frozen=True)
class Ask:
    """Speak, then listen. The runtime answers with Heard/Silence (keywords) or Reported/Silence
    (transcript), carrying this ask_id. Answers to older asks are ignored."""
    ask_id: int
    text: str
    listen: Literal["keywords", "transcript"]
    loud: bool = False


@dataclass(frozen=True)
class Escalate:
    """Fire neighbors (if include_neighbors), family and 911 at once. Answer with EscalationSent."""
    incident_id: str
    include_neighbors: bool


@dataclass(frozen=True)
class PauseEscalation:
    incident_id: str


@dataclass(frozen=True)
class ResumeEscalation:
    incident_id: str


@dataclass(frozen=True)
class SendUpdate:
    """Templated update to family + 911 (and the map) around a verbatim quote."""
    incident_id: str
    status: str
    quote: str
    source: str


@dataclass(frozen=True)
class Publish:
    """An event for the map. Never contains video or face data."""
    payload: dict


@dataclass(frozen=True)
class LogLocal:
    """Kept on the robot only (false alarms, recoveries). Never sent."""
    kind: str
    detail: str


Action = Union[SetEyes, Chirp, StartAlarm, StopAlarm, Say, Ask, Escalate, PauseEscalation,
               ResumeEscalation, SendUpdate, Publish, LogLocal]


# ---------------------------------------------------------------- state

@dataclass(frozen=True)
class Entry:
    ts: float
    kind: str
    detail: str


@dataclass(frozen=True)
class Incident:
    id: str
    event: EventKind
    who: Who
    started_at: float
    timeline: tuple[Entry, ...] = ()
    alerts_sent: bool = False
    channels: tuple[str, ...] = ()
    neighbor: Literal["none", "alerted", "on_the_way", "with_her"] = "none"
    helper: str | None = None
    outcome: str | None = None

    def log(self, ts: float, kind: str, detail: str = "") -> "Incident":
        return replace(self, timeline=self.timeline + (Entry(ts, kind, detail),))


@dataclass(frozen=True)
class Robot:
    mode: Mode = "MONITORING"
    incident: Incident | None = None
    last_ask_id: int = 0
    awaiting: Awaiting | None = None
    asks_in_round: int = 0
    resident_posture: Posture = "upright"  # posture at the last button press (for the onscene line)
    closed: tuple[Incident, ...] = field(default=())  # finished incidents, newest last


def attribute_press(resident_posture: Posture) -> Who:
    """Who pressed the button, from what the camera saw at that moment.
    Ruth upright or sitting: it was Ruth. Ruth lying down or out of view: someone else."""
    return "resident" if resident_posture in ("upright", "sitting") else "other"


# ---------------------------------------------------------------- machine

class Machine:
    def __init__(self, profile: Profile | None = None, asks: int = 2) -> None:
        self.p = profile or Profile()
        self.asks = asks

    # ---- public

    def step(self, s: Robot, e: Event) -> tuple[Robot, list[Action]]:
        handler = {
            "MONITORING": self._monitoring,
            "CHECK_IN": self._check_in,
            "ALERTED": self._alerted,
        }[s.mode]
        if isinstance(e, Reset) and s.mode != "MONITORING":
            return self._close(s, "reset", e.ts, [])
        if isinstance(e, (Heard, Silence)) or (isinstance(e, Reported) and e.ask_id is not None):
            if e.ask_id != s.last_ask_id or s.awaiting is None:
                return s, []  # stale answer to an older question
        return handler(s, e)

    # ---- MONITORING

    def _monitoring(self, s: Robot, e: Event) -> tuple[Robot, list[Action]]:
        if isinstance(e, Detected):
            return self._start(s, e.kind, e.who, e.ts)
        if isinstance(e, ManualTrigger):
            return self._start(s, "manual", "resident", e.ts)
        return s, []  # button presses, stray replies: nothing to do

    def _start(self, s: Robot, kind: EventKind, who: Who, ts: float):
        inc = Incident(id=f"inc-{int(ts * 1000)}", event=kind, who=who, started_at=ts)
        inc = inc.log(ts, "detected", f"{kind} ({who})")
        s = replace(s, mode="CHECK_IN", incident=inc)
        s, ask = self._ask(s, "check_in", 1, ts)
        return s, [SetEyes("checking"), Chirp(), ask,
                   Publish({"type": "status", "mode": "CHECK_IN", "incident_id": inc.id,
                            "event": kind, "who": who, "ts": ts})]

    # ---- CHECK_IN

    def _check_in(self, s: Robot, e: Event) -> tuple[Robot, list[Action]]:
        inc = s.incident
        assert inc is not None
        if isinstance(e, Heard):
            inc = inc.log(e.ts, "response", f"{e.intent}: {e.text}")
            s = replace(s, incident=inc)
            if s.awaiting == "onscene":
                return self._ask_again_or(s, e.ts, lambda: self._alert(s, e.ts, include_neighbors=False))
            if e.intent == "ok":
                via = "button+voice" if s.awaiting == "confirm" else "voice"
                return self._close(s, "ok", e.ts, [Say(script.glad_ok(self.p, inc.who)),
                                                   LogLocal("check_in_ok", f"{inc.event} ({via})")])
            if e.intent == "help":
                return self._alert(s, e.ts)
            return self._ask_again_or(s, e.ts, lambda: self._alert(s, e.ts))
        if isinstance(e, Silence):
            s = replace(s, incident=inc.log(e.ts, "response", "silence"))
            note = "button pressed, no spoken response" if s.awaiting == "confirm" else None
            return self._ask_again_or(s, e.ts, lambda: self._alert(s, e.ts, note=note,
                                                                   include_neighbors=s.awaiting != "onscene"))
        if isinstance(e, ButtonPressed):
            return self._button(s, e)
        if isinstance(e, Reported) and e.source == "in_room" and s.awaiting == "onscene":
            # Someone else is with her before any alert went out. Only the fallen person's own
            # "I'm OK" cancels a check-in, so we escalate (without neighbors: a person is there)
            # and pass on exactly what they said.
            if e.status == "unclear" and s.asks_in_round < 2:
                s = replace(s, incident=inc.log(e.ts, "report", f"unclear: {e.quote!r}"))
                s, ask = self._ask(s, "onscene", s.asks_in_round + 1, e.ts)
                return s, [ask]
            s2, actions = self._alert(s, e.ts, include_neighbors=False)
            s3, more = self._report(s2, e)
            return s3, actions + more
        return s, []

    # ---- ALERTED

    def _alerted(self, s: Robot, e: Event) -> tuple[Robot, list[Action]]:
        inc = s.incident
        assert inc is not None
        if isinstance(e, EscalationSent):
            inc = replace(inc, alerts_sent=True, channels=e.channels,
                          neighbor="alerted" if "neighbors" in e.channels and inc.neighbor == "none" else inc.neighbor)
            inc = inc.log(e.ts, "escalated", ", ".join(e.channels) or "nothing reached")
            return replace(s, incident=inc), [Say(script.getting_help(self.p, e.channels))]
        if isinstance(e, ReceiverReply):
            inc = inc.log(e.ts, "receiver", f"{e.name}: {e.reply}")
            s = replace(s, incident=inc)
            if e.reply == "yes" and inc.neighbor in ("none", "alerted"):
                inc = replace(inc, neighbor="on_the_way", helper=e.name)
                return replace(s, incident=inc), [
                    SetEyes("help_coming"), Say(script.helper_coming(e.name, e.street)),
                    Publish({"type": "update", "incident_id": inc.id, "status": "on_the_way",
                             "helper": e.name, "ts": e.ts})]
            return s, []
        if isinstance(e, CallUpdate):
            s = replace(s, incident=inc.log(e.ts, "call", f"{e.target}: {e.status}"))
            if e.target == "911" and e.status == "unanswered":
                return s, [Say(script.psap_unanswered())]
            return s, []
        if isinstance(e, ButtonPressed):
            return self._button(s, e)
        if isinstance(e, Heard):
            s = replace(s, incident=inc.log(e.ts, "response", f"{e.intent}: {e.text}"))
            if s.awaiting == "confirm" and e.intent == "ok":
                quote = Reported("she_is_ok", e.text, "in_room", e.ts)
                return self._report(s, quote, speaker="resident")
            if s.awaiting == "confirm" and e.intent == "help":
                return self._resume(s, [Say(script.still_getting_help())])
            if s.awaiting == "onscene":
                return self._ask_again_or(s, e.ts, lambda: self._resume(s))
            return self._ask_again_or(s, e.ts, lambda: self._resume(s))
        if isinstance(e, Silence):
            detail = "button pressed, no spoken response" if s.awaiting == "confirm" else "no explanation after button"
            s = replace(s, incident=inc.log(e.ts, "response", "silence"))
            return self._ask_again_or(s, e.ts, lambda: self._resume(
                replace(s, incident=s.incident.log(e.ts, "note", detail))))
        if isinstance(e, Reported):
            return self._report(s, e)
        return s, []

    # ---- shared pieces

    def _ask(self, s: Robot, kind: Awaiting, n: int, ts: float) -> tuple[Robot, Ask]:
        inc = s.incident
        assert inc is not None
        if kind == "check_in":
            text, listen = script.check_in(self.p, inc.event, inc.who, n), "keywords"
        elif kind == "confirm":
            text, listen = script.confirm(self.p, inc.who, n), "keywords"
        else:
            text, listen = script.onscene(self.p, inc.who, s.resident_posture, n), "transcript"
        ask_id = s.last_ask_id + 1
        s = replace(s, last_ask_id=ask_id, awaiting=kind, asks_in_round=n,
                    incident=inc.log(ts, "ask", f"{kind} #{n}"))
        return s, Ask(ask_id, text, listen, loud=n > 1)

    def _ask_again_or(self, s: Robot, ts: float, otherwise):
        """Ask the same question again, up to the limit, then fall through to `otherwise`.
        The onscene question is asked at most twice; check-in and confirm use `self.asks`."""
        assert s.awaiting is not None
        limit = 2 if s.awaiting == "onscene" else self.asks
        if s.asks_in_round < limit:
            s, ask = self._ask(s, s.awaiting, s.asks_in_round + 1, ts)
            return s, ([Chirp()] if s.awaiting == "check_in" else []) + [ask]
        return otherwise()

    def _button(self, s: Robot, e: ButtonPressed):
        inc = s.incident
        assert inc is not None
        by = attribute_press(e.resident_posture)
        inc = inc.log(e.ts, "button", f"pressed by {by} (resident {e.resident_posture})")
        s = replace(s, incident=inc, resident_posture=e.resident_posture)
        actions: list[Action] = [StopAlarm()]
        if s.mode == "ALERTED":
            actions.append(PauseEscalation(inc.id))
        s, ask = self._ask(s, "confirm" if by == "resident" else "onscene", 1, e.ts)
        return s, actions + [ask]

    def _alert(self, s: Robot, ts: float, note: str | None = None, include_neighbors: bool = True):
        inc = s.incident
        assert inc is not None
        if note:
            inc = inc.log(ts, "note", note)
        inc = inc.log(ts, "alerted", "")
        s = replace(s, mode="ALERTED", incident=inc, awaiting=None, asks_in_round=0)
        return s, [SetEyes("alerted"), StartAlarm(), Escalate(inc.id, include_neighbors),
                   Publish({"type": "alert", "incident_id": inc.id, "event": inc.event,
                            "who": inc.who, "ts": ts})]

    def _resume(self, s: Robot, extra: list[Action] | None = None):
        inc = s.incident
        assert inc is not None
        s = replace(s, awaiting=None, asks_in_round=0)
        eyes = "help_coming" if inc.neighbor in ("on_the_way", "with_her") else "alerted"
        return s, (extra or []) + [SetEyes(eyes), StartAlarm(), ResumeEscalation(inc.id)]

    def _report(self, s: Robot, e: Reported, speaker: str = "someone"):
        inc = s.incident
        assert inc is not None
        inc = inc.log(e.ts, "report", f"{e.status} via {e.source}: {e.quote!r}")
        s = replace(s, incident=inc)
        if e.status == "unclear" and e.source == "in_room" and s.awaiting == "onscene" and s.asks_in_round < 2:
            s, ask = self._ask(s, "onscene", s.asks_in_round + 1, e.ts)
            return s, [ask]
        source = "resident" if speaker == "resident" else e.source
        actions: list[Action] = [SendUpdate(inc.id, e.status, e.quote, source),
                                 Publish({"type": "update", "incident_id": inc.id, "status": e.status,
                                          "quote": e.quote, "source": source, "ts": e.ts})]
        if e.source == "in_room":
            actions.insert(0, Say(script.passed_on(self.p)))
            if speaker == "resident":
                actions.insert(0, Say(script.glad_ok(self.p, inc.who)))
        if e.status in CLOSING_REPORTS:
            return self._close(replace(s, awaiting=None), e.status, e.ts, actions)
        if e.status == "with_her":
            inc = replace(inc, neighbor="with_her")
            return replace(s, incident=inc, awaiting=None), actions + [StopAlarm(), SetEyes("help_coming")]
        return replace(s, awaiting=None), actions  # unclear: passed on as-is, stay alerted

    def _close(self, s: Robot, outcome: str, ts: float, actions: list[Action]):
        inc = s.incident
        assert inc is not None
        inc = replace(inc, outcome=outcome).log(ts, "closed", outcome)
        done = Robot(mode="MONITORING", last_ask_id=s.last_ask_id, closed=s.closed + (inc,))
        out = list(actions) + [StopAlarm(), SetEyes("watching")]
        if inc.alerts_sent or s.mode == "ALERTED":
            out.append(Publish({"type": "close", "incident_id": inc.id, "outcome": outcome, "ts": ts}))
        else:
            out.append(Publish({"type": "status", "mode": "MONITORING", "ts": ts}))
        return done, out
