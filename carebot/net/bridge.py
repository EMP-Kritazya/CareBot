"""The robot's side of the map link: events out, commands in, neighbors in waves.

    out  event      every Publish from the state machine (+ sender, ISO time), and a heartbeat
                    (mode, eye color) every few seconds so a map opened late catches up
    out  receivers  each neighbor's status (Waves)
    in   cmd        trigger -> ManualTrigger, reset -> Reset, receiver_reply -> ReceiverReply

Only JSON with ids, statuses, times and verbatim quotes. Never video, never face data.
"""

from __future__ import annotations

import datetime as dt
import time

from carebot.core import state as st
from carebot.escalate.receivers import SimReceivers, Waves, load_receivers, load_routes, rank


def iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


class RobotLink:
    def __init__(self, transport, runtime, unit: str = "robot-4A", waves: Waves | None = None,
                 heartbeat_s: float = 3.0, clock=time.time) -> None:
        self.transport, self.runtime, self.unit, self.waves = transport, runtime, unit, waves
        self.heartbeat_s, self.clock = heartbeat_s, clock
        self._last_beat = 0.0
        runtime.publish = self.publish_event
        runtime.tickers.append(self.tick)
        if waves is not None:
            runtime.observers.append(waves.on_event)
        transport.subscribe("cmd", self.on_cmd)

    def publish_event(self, payload: dict) -> None:
        msg = {**payload, "sender": self.unit}
        if isinstance(msg.get("ts"), (int, float)):
            msg["ts"] = iso(msg["ts"])
        self.transport.publish("event", msg)
        if payload.get("type") in ("alert", "close", "update"):
            self._last_beat = 0.0  # a heartbeat right after, with the new mode and eye color

    def report(self, target: str, kind: str, channel: str, status: str, simulated: bool = False) -> None:
        """Live delivery status of a call or text (911 / family), for the map's dispatch board."""
        inc = self.runtime.state.incident or (self.runtime.state.closed[-1] if self.runtime.state.closed else None)
        self.transport.publish("event", {"type": "dispatch", "sender": self.unit, "incident_id": inc.id if inc else None,
                                         "target": target, "kind": kind, "channel": channel, "status": status,
                                         "simulated": simulated, "ts": iso(self.clock())})

    def heartbeat(self) -> dict:
        s = self.runtime.state
        inc = s.incident
        return {"type": "heartbeat", "sender": self.unit, "mode": s.mode, "eyes": self.runtime.eyes,
                "incident_id": inc.id if inc else None, "neighbor": inc.neighbor if inc else None,
                "helper": inc.helper if inc else None, "awaiting": s.awaiting, "ask": s.asks_in_round,
                "ts": iso(self.clock())}

    def tick(self) -> None:
        if self.waves is not None:
            s = self.runtime.state
            inc = self.waves.incident
            self.waves.tick(active=bool(s.mode == "ALERTED" and inc and s.incident and s.incident.id == inc.id))
        now = self.clock()
        if now - self._last_beat >= self.heartbeat_s:
            self._last_beat = now
            self.transport.publish("event", self.heartbeat())

    def on_cmd(self, msg: dict) -> None:
        kind, now = msg.get("type"), self.clock()
        if kind == "trigger":
            print("  <- map: Trigger alert", flush=True)
            self.runtime.post(st.ManualTrigger(now))
        elif kind == "reset":
            print("  <- map: Reset", flush=True)
            self.runtime.post(st.Reset(now))
        elif kind == "receiver_reply" and self.waves is not None and msg.get("reply") in ("yes", "no"):
            r = self.waves.by_id(str(msg.get("id")))
            if r is not None:
                print(f"  <- {r.name} replied {msg['reply'].upper()} (simulated on the map)", flush=True)
                self.runtime.post(st.ReceiverReply(r.id, r.name, r.street, msg["reply"], now))


def attach_map(runtime, notifier, transport, cfg: dict, demo: bool = False, receivers_path: str = "config/receivers.yaml",
               routes_path: str = "data/routes.json") -> RobotLink:
    """Neighbors in waves (if config/receivers.yaml exists) + the link to the map."""
    rc = load_receivers(receivers_path)
    waves = None
    if rc["receivers"] and rc["home"]:
        w = rc["waves"]
        call = getattr(notifier, "call_receiver", None)
        if getattr(notifier, "dry_run", False):
            call = None
        waves = Waves(rank(rc["home"], rc["receivers"], load_routes(routes_path)),
                      publish=lambda m: transport.publish("receivers", m), contact_call=call,
                      wave_size=int(w.get("wave_size", 3)),
                      wave_s=float(w.get("wave_s_demo" if demo else "wave_s", 25 if demo else 90)))
        notifier.neighbors = waves
    link = RobotLink(transport, runtime, cfg.get("unit_id", "robot-4A"), waves)
    notifier.report = link.report
    if hasattr(notifier, "text_channel"):
        notifier.text_channel = cfg.get("twilio", {}).get("texts", "sms")
    return link


def run_sim_receivers(transport, receivers_path: str = "config/receivers.yaml",
                      yes_after_s: float | None = None) -> SimReceivers:
    """Play the neighbors without real phones, in this process (the map server does this by default)."""
    sim = SimReceivers(load_receivers(receivers_path)["receivers"],
                       reply=lambda rid, ans: transport.publish("cmd", {"type": "receiver_reply", "id": rid,
                                                                         "reply": ans}),
                       yes_after_s=yes_after_s)
    transport.subscribe("receivers", sim.on_receivers)
    return sim
