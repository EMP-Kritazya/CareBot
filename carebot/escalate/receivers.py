"""Neighbors who agreed to help: ranking by walking time, and contacting them in waves.

    Waves       on the robot. Wave 1 = the nearest `wave_size`; if nobody says YES within `wave_s`,
                the next wave. After a YES, everyone else still waiting is stood down. The button
                (PauseEscalation) holds the clock; closing the incident ends it.
    SimReceivers on the map server (or in-process): plays the neighbors without real phones, replying
                yes / no / nothing as `config/receivers.yaml` says.

Every status change goes out as one `receivers` message: {incident_id, wave, statuses: {id: status}}.
Statuses: waiting (not contacted yet), contacted, yes, no, no_reply, stood_down.
Only ids and statuses travel; names and positions live in the config on both machines.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml

from carebot.core import state as st

WALK_M_PER_S = 1.2     # an unhurried adult, for the straight-line fallback
DETOUR = 1.35          # city blocks are longer than a straight line


@dataclass(frozen=True)
class Receiver:
    id: str
    name: str
    lat: float
    lng: float
    street: str | None = None
    channel: str = "sim"            # sim | call
    phone_env: str | None = None    # the .env variable with a real receiver's number
    note: str = ""
    sim_reply: str = "none"         # yes | no | none
    sim_after_s: float = 10


def load_receivers(path: str | Path = "config/receivers.yaml") -> dict:
    """{home: {...}, waves: {...}, receivers: [Receiver]}. Missing file: no receivers."""
    p = Path(path)
    if not p.exists():
        return {"home": None, "waves": {}, "receivers": []}
    raw = yaml.safe_load(p.read_text()) or {}
    out = []
    for r in raw.get("receivers", []):
        sim = r.get("sim") or {}
        reply = sim.get("reply", "none")
        reply = {True: "yes", False: "no", None: "none"}.get(reply, reply) if isinstance(reply, (bool, type(None))) \
            else str(reply).lower()  # YAML reads a bare yes / no as true / false
        out.append(Receiver(id=str(r["id"]), name=r["name"], lat=float(r["lat"]), lng=float(r["lng"]),
                            street=r.get("street"), channel=r.get("channel", "sim"), phone_env=r.get("phone_env"),
                            note=r.get("note", "") or "", sim_reply=reply,
                            sim_after_s=float(sim.get("after_s", 10))))
    return {"home": raw.get("home"), "waves": raw.get("waves", {}), "receivers": out}


def load_routes(path: str | Path = "data/routes.json") -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text()).get("routes", {})
    except (json.JSONDecodeError, AttributeError):
        return {}


def straight_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Haversine distance in meters."""
    lat1, lng1, lat2, lng2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def walk_s(home: dict, r: Receiver, routes: dict) -> float:
    """Walking seconds from the receiver to Sandy's home: the precomputed route, else straight line."""
    route = routes.get(r.id)
    if route and route.get("walk_s"):
        return float(route["walk_s"])
    return straight_m((r.lat, r.lng), (home["lat"], home["lng"])) * DETOUR / WALK_M_PER_S


def rank(home: dict, receivers: list[Receiver], routes: dict) -> list[Receiver]:
    return sorted(receivers, key=lambda r: (walk_s(home, r, routes), r.id))


@dataclass
class Waves:
    """Contacts receivers in waves. Ticked from the runtime loop (no threads of its own), so it is
    deterministic and testable with a fake clock.

    contact_call(receiver, incident) -> bool places a real call (Twilio); None = treat as simulated.
    publish(dict) sends the `receivers` message (the map)."""

    ranked: list[Receiver]
    publish: Callable[[dict], None]
    contact_call: Callable[[Receiver, st.Incident], bool] | None = None
    wave_size: int = 3
    wave_s: float = 90
    clock: Callable[[], float] = time.time
    env: dict = field(default_factory=lambda: os.environ)

    def __post_init__(self) -> None:
        self._lock = threading.RLock()
        self.incident: st.Incident | None = None
        self.statuses: dict[str, str] = {}
        self.real: set[str] = set()   # receivers reached on their real phone (the simulator leaves them alone)
        self.wave = 0
        self.deadline: float | None = None
        self.paused_at: float | None = None
        self.done = True

    # ---- the incident

    def start(self, incident: st.Incident) -> bool:
        """Contact the first wave. True if anyone was reached (so CareBot may say "neighbors")."""
        with self._lock:
            self.incident, self.wave, self.done, self.paused_at = incident, 0, False, None
            self.statuses = {r.id: "waiting" for r in self.ranked}
            self.real = set()
            reached = self._next_wave()
            self._send()
            return reached

    def _next_wave(self) -> bool:
        batch = [r for r in self.ranked if self.statuses.get(r.id) == "waiting"][: self.wave_size]
        if not batch:
            self.done, self.deadline = True, None
            return False
        self.wave += 1
        reached = False
        for r in batch:
            if self._contact(r):
                self.statuses[r.id] = "contacted"
                reached = True
            else:
                self.statuses[r.id] = "no_reply"  # couldn't be reached at all
        self.deadline = self.clock() + self.wave_s
        if not reached:
            return self._next_wave()
        return True

    def _contact(self, r: Receiver) -> bool:
        if r.channel == "call" and self.contact_call is not None and self.env.get(r.phone_env or "", ""):
            self.real.add(r.id)
            return bool(self.contact_call(r, self.incident))
        return True  # simulated: the map server plays this neighbor

    def tick(self, active: bool) -> None:
        """Called every loop. `active`: the incident is still open and alerted."""
        with self._lock:
            if self.done or self.incident is None:
                return
            if not active:
                self.done = True
                return
            if self.paused_at is not None or self.deadline is None:
                return
            if self.clock() >= self.deadline:
                for rid, s in self.statuses.items():
                    if s == "contacted":
                        self.statuses[rid] = "no_reply"
                self._next_wave()
                self._send()

    def on_event(self, e) -> None:
        """Every runtime event passes here; only neighbor replies matter."""
        if not isinstance(e, st.ReceiverReply):
            return
        with self._lock:
            if self.incident is None or e.receiver_id not in self.statuses:
                return
            self.statuses[e.receiver_id] = e.reply
            if e.reply == "yes" and not self.done:
                self.done, self.deadline = True, None
                for rid, s in self.statuses.items():
                    if s == "contacted":  # texted and still waiting: "thanks, someone is on the way"
                        self.statuses[rid] = "stood_down"
            elif e.reply == "no" and not any(s == "contacted" for s in self.statuses.values()) and not self.done:
                self.deadline = self.clock()  # everyone in this wave answered: don't wait, next wave
            self._send()

    def pause(self) -> None:
        with self._lock:
            if self.paused_at is None:
                self.paused_at = self.clock()

    def resume(self) -> None:
        with self._lock:
            if self.paused_at is not None and self.deadline is not None:
                self.deadline += self.clock() - self.paused_at
            self.paused_at = None

    def snapshot(self) -> dict:
        with self._lock:
            return {"type": "receivers", "incident_id": self.incident.id if self.incident else None,
                    "wave": self.wave, "statuses": dict(self.statuses), "real": sorted(self.real),
                    "ts": self.clock()}

    def _send(self) -> None:
        self.publish(self.snapshot())

    def by_id(self, rid: str) -> Receiver | None:
        return next((r for r in self.ranked if r.id == rid), None)


class SimReceivers:
    """Plays the neighbors who have no real phone. Watches `receivers` messages; when a simulated
    neighbor becomes `contacted`, it replies after `sim_after_s` (or never, for `none`).

    reply(receiver_id, "yes" | "no") sends the answer back (cmd topic: receiver_reply).
    real_ids: receivers never simulated (the robot also lists the ones it really called in `real`)."""

    def __init__(self, receivers: list[Receiver], reply: Callable[[str, str], None],
                 real_ids: set[str] | None = None, yes_after_s: float | None = None) -> None:
        self.receivers = {r.id: r for r in receivers}
        self.reply = reply
        self.real_ids = real_ids or set()
        self.yes_after_s = yes_after_s  # override for every "yes" (e.g. --sim-neighbor 20)
        self._timers: dict[tuple[str, str], threading.Timer] = {}
        self._lock = threading.Lock()

    def on_receivers(self, msg: dict) -> None:
        inc = msg.get("incident_id")
        real = self.real_ids | set(msg.get("real") or [])
        for rid, status in (msg.get("statuses") or {}).items():
            r = self.receivers.get(rid)
            if r is None or rid in real or r.sim_reply not in ("yes", "no"):
                continue
            key = (inc, rid)
            with self._lock:
                if status == "contacted" and key not in self._timers:
                    after = self.yes_after_s if (r.sim_reply == "yes" and self.yes_after_s is not None) else r.sim_after_s
                    t = threading.Timer(after, self._fire, args=(key, rid, r.sim_reply))
                    t.daemon = True
                    self._timers[key] = t
                    t.start()
                elif status in ("stood_down", "no_reply") and key in self._timers:
                    self._timers.pop(key).cancel()
                    self._timers[key] = _Done()

    def _fire(self, key, rid: str, answer: str) -> None:
        with self._lock:
            if not isinstance(self._timers.get(key), threading.Timer):
                return
            self._timers[key] = _Done()
        self.reply(rid, answer)

    def stop(self) -> None:
        with self._lock:
            for t in self._timers.values():
                t.cancel()


class _Done:
    def cancel(self) -> None:
        pass
