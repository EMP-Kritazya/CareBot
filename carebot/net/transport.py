"""The link between the robot and the map: JSON messages on three topics, nothing else.

    carebot/<unit>/event       robot -> map    alert, status, update, close, sent, call, heartbeat
    carebot/<unit>/receivers   robot -> map    each neighbor's status during an incident
    carebot/<unit>/cmd         map -> robot    trigger, reset, receiver_reply (simulated neighbors)

Two implementations with the same three methods:
    MemoryTransport  one process (laptop: watcher + map together, tests)
    MqttTransport    two machines over our router; the robot runs the Mosquitto broker

Privacy: only these small JSON messages ever cross the network. Never video, never face data.
"""

from __future__ import annotations

import json
import threading
from typing import Callable

Handler = Callable[[dict], None]
TOPICS = ("event", "receivers", "cmd")


def topic(unit: str, name: str) -> str:
    return f"carebot/{unit}/{name}"


class MemoryTransport:
    """In-process: publish calls every subscriber of that topic directly (on the caller's thread)."""

    name = "in-memory"

    def __init__(self, unit: str = "robot-4A") -> None:
        self.unit = unit
        self._subs: dict[str, list[Handler]] = {}
        self._lock = threading.Lock()

    def publish(self, name: str, payload: dict) -> None:
        msg = json.loads(json.dumps(payload))  # same as the wire: JSON-safe copies only
        with self._lock:
            subs = list(self._subs.get(name, []))
        for fn in subs:
            try:
                fn(msg)
            except Exception as e:  # noqa: BLE001 - one bad subscriber must not break the robot
                print(f"  (transport: a '{name}' subscriber failed: {e})", flush=True)

    def subscribe(self, name: str, fn: Handler) -> None:
        with self._lock:
            self._subs.setdefault(name, []).append(fn)

    def close(self) -> None:
        pass


class MqttTransport:
    """paho-mqtt client. Reconnects by itself; messages published while offline are dropped
    (the robot's heartbeat repeats the current state every few seconds anyway)."""

    def __init__(self, host: str = "localhost", port: int = 1883, unit: str = "robot-4A",
                 client_id: str | None = None) -> None:
        import paho.mqtt.client as mqtt  # lazy: laptops on the memory transport never need it

        self.unit, self.host, self.port = unit, host, port
        self.name = f"MQTT {host}:{port}"
        self._subs: dict[str, list[Handler]] = {}
        self._lock = threading.Lock()
        self.connected = threading.Event()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id or "")
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = lambda *a, **k: self.connected.clear()
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=5)
        self.client.connect_async(host, port, keepalive=30)
        self.client.loop_start()

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        if getattr(reason_code, "is_failure", False):
            print(f"  (MQTT: {self.host} refused the connection: {reason_code})", flush=True)
            return
        self.connected.set()
        with self._lock:
            names = list(self._subs)
        for name in names:  # (re)subscribe after every connect
            client.subscribe(topic(self.unit, name), qos=1)

    def _on_message(self, client, userdata, message) -> None:
        name = message.topic.rsplit("/", 1)[-1]
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return
        with self._lock:
            subs = list(self._subs.get(name, []))
        for fn in subs:
            try:
                fn(payload)
            except Exception as e:  # noqa: BLE001
                print(f"  (MQTT: a '{name}' subscriber failed: {e})", flush=True)

    def publish(self, name: str, payload: dict) -> None:
        self.client.publish(topic(self.unit, name), json.dumps(payload), qos=1)

    def subscribe(self, name: str, fn: Handler) -> None:
        with self._lock:
            first = name not in self._subs
            self._subs.setdefault(name, []).append(fn)
        if first and self.connected.is_set():
            self.client.subscribe(topic(self.unit, name), qos=1)

    def wait_connected(self, timeout: float = 5) -> bool:
        return self.connected.wait(timeout)

    def close(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()


def make_transport(cfg: dict, override_host: str | None = None):
    """From the config's `transport:` section: kind memory | mqtt (host, port)."""
    t = cfg.get("transport", {})
    unit = cfg.get("unit_id", "robot-4A")
    if override_host or t.get("kind") == "mqtt":
        host = override_host or t.get("host", "localhost")
        tr = MqttTransport(host, int(t.get("port", 1883)), unit)
        if not tr.wait_connected(t.get("connect_timeout_s", 3)):
            print(f"  (MQTT: no broker at {host}:{tr.port} yet; retrying in the background)", flush=True)
        return tr
    return MemoryTransport(unit)
