"""The robot's runtime: events in from everywhere, the state machine decides, actions go out.

Threads post events (`post`), and the main loop calls `pump()` once per camera frame. `pump` runs
every queued event through the pure state machine and executes the resulting actions:

    SetEyes / Chirp / Start-StopAlarm   -> hardware (Pi drivers or mocks)
    Say / Ask                           -> the voice worker (answers come back as events)
    Escalate / Pause / Resume / Update  -> the notifier (console now, Twilio next)
    Publish                             -> the map (carebot/net/bridge.py: MQTT or in-memory)
    LogLocal                            -> stays on the robot
    RestartStillness                    -> vision (stillness timer, cooldown, back-off)

The decisions all live in core/state.py; this file only wires them to the world.
"""

from __future__ import annotations

import datetime as dt
import queue
import time
from typing import Callable

from carebot.core import state as st
from carebot.core.narrate import DIM, RESET, describe

BUTTON = "button"  # posted by the button callback; turned into ButtonPressed with the camera's view


class Runtime:
    def __init__(self, machine: st.Machine, hw, voice, notifier, publish: Callable[[dict], None] | None = None,
                 posture: Callable[[], str] = lambda: "upright", narrate: bool = True,
                 clock: Callable[[], float] = time.time,
                 restart_stillness: Callable[[bool, bool], None] = lambda cooldown, backoff: None,
                 comfort_every_s: float | None = None) -> None:
        self.machine, self.hw, self.voice, self.notifier = machine, hw, voice, notifier
        self.publish = publish or (lambda payload: None)
        self.posture = posture
        self.restart_stillness = restart_stillness
        self.comfort_every_s = comfort_every_s
        self._idle_since: float | None = None  # when ALERTED with no question pending began
        self.narrate = narrate
        self.clock = clock
        self.state = st.Robot()
        self.events: queue.Queue = queue.Queue()
        self.last_said: str | None = None
        self.eyes = "watching"                         # the last eye state (the map shows it)
        self.observers: list[Callable[[object], None]] = []  # see every event first (e.g. neighbor waves)
        self.tickers: list[Callable[[], None]] = []    # called every pump (e.g. wave timers, heartbeat)
        hw.button.on_press(lambda _ts: self.post(BUTTON))
        hw.eyes.set("watching")

    def post(self, event) -> None:
        """Thread-safe: any thread may post."""
        self.events.put(event)

    def pump(self) -> list:
        done = []
        while True:
            try:
                e = self.events.get_nowait()
            except queue.Empty:
                self._comfort_timer()
                for tick in self.tickers:
                    try:
                        tick()
                    except Exception as ex:  # noqa: BLE001 - a map or wave problem must not stop the robot
                        print(f"  (runtime: a ticker failed: {ex})", flush=True)
                return done
            if e == BUTTON:
                e = st.ButtonPressed(self.posture(), self.clock())
            for watch in self.observers:
                try:
                    watch(e)
                except Exception as ex:  # noqa: BLE001
                    print(f"  (runtime: an observer failed: {ex})", flush=True)
            self.state, actions = self.machine.step(self.state, e)
            for a in actions:
                self._execute(a)
            done.extend(actions)

    def _comfort_timer(self) -> None:
        """While alerted and idle, open a comfort window every comfort_every_s."""
        s = self.state
        if not self.comfort_every_s or s.mode != "ALERTED" or s.awaiting is not None:
            self._idle_since = None
            return
        now = self.clock()
        if self._idle_since is None:
            self._idle_since = now
        elif now - self._idle_since >= self.comfort_every_s:
            self._idle_since = None
            self.post(st.ComfortWindow(now))

    def _execute(self, a) -> None:
        if self.narrate:
            line = describe(a)
            if line:
                print(f"{DIM}{dt.datetime.now():%H:%M:%S}{RESET}  {line}", flush=True)
        hw = self.hw
        if isinstance(a, st.SetEyes):
            self.eyes = a.state
            hw.eyes.set(a.state)
        elif isinstance(a, st.Chirp):
            hw.buzzer.chirp()
        elif isinstance(a, st.StartAlarm):
            hw.buzzer.start_alarm()
        elif isinstance(a, st.StopAlarm):
            hw.buzzer.stop_alarm()
        elif isinstance(a, (st.Say, st.Ask)):
            self.last_said = a.text
            self.voice.submit(a)
        elif isinstance(a, st.Escalate):
            self.notifier.escalate(self._incident(a.incident_id), a.include_neighbors, a.family_only)
        elif isinstance(a, st.PauseEscalation):
            self.notifier.pause()
        elif isinstance(a, st.ResumeEscalation):
            self.notifier.resume()
        elif isinstance(a, st.SendUpdate):
            self.notifier.update(a)
        elif isinstance(a, st.Publish):
            try:
                self.publish(a.payload)
            except Exception as ex:  # noqa: BLE001 - the map is optional; the robot keeps going
                print(f"  (runtime: couldn't publish to the map: {ex})", flush=True)
        elif isinstance(a, st.RestartStillness):
            self.restart_stillness(a.cooldown, a.backoff)

    def _incident(self, incident_id: str) -> st.Incident:
        """The incident an action refers to (it may have just closed, e.g. a family-only alert)."""
        if self.state.incident is not None and self.state.incident.id == incident_id:
            return self.state.incident
        return next(i for i in reversed(self.state.closed) if i.id == incident_id)

    def status(self) -> str:
        s = self.state
        if s.mode == "MONITORING":
            return "CareBot: watching"
        if s.mode == "CHECK_IN":
            what = {"check_in": "asking if they're OK", "confirm": "waiting for a spoken 'I'm OK'",
                    "onscene": "asking who's there"}.get(s.awaiting or "", "checking in")
            return f"CareBot: CHECK-IN ({what}, ask {s.asks_in_round})"
        inc = s.incident
        extra = {"on_the_way": ", a neighbor is on the way", "with_resident": ", someone is with him"}.get(
            inc.neighbor if inc else "", "")
        if s.awaiting == "comfort":
            extra += " (talking with him: listening)"
        return f"CareBot: ALERTED{extra}"
