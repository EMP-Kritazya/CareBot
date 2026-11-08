"""The laptop's microphone as the robot's ears (presentation: the robot has no mic of its own in the hall).

    python -m apps.remote_mic --mqtt carebot.local
    python -m apps.remote_mic --mqtt carebot.local --device "MacBook Pro Microphone"

Runs on the laptop, next to the person speaking. It transcribes with Vosk on the laptop and sends the
robot **text only** (cmd topic: {"type": "heard", "text": ...}). No audio goes over the network.

It only transcribes while the robot is listening: the robot announces "listening on" after it has
finished speaking (event topic: {"type": "listening", "on": true}), and "off" when the answer window
closes. So the robot's own voice from the JBL is never sent back as an answer, and at most one answer
is sent per question.
"""

from __future__ import annotations

import argparse
import json
import queue
import threading
import time
from dataclasses import dataclass, field


@dataclass
class Gate:
    """Pure: when may a transcript be sent? Only while the robot listens, and once per question."""
    skip_s: float = 0.3            # ignore this much audio after "listening on" (the speaker's last echo)
    listening: bool = False
    since: float = 0.0
    sent: bool = False
    generation: int = 0            # bumps on every new question: start a fresh recognizer
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def set(self, on: bool, now: float) -> None:
        with self._lock:
            if on and not self.listening:
                self.generation += 1
                self.since, self.sent = now, False
            self.listening = on

    def hearing(self, now: float) -> bool:
        """Should audio be fed to the recognizer right now?"""
        with self._lock:
            return self.listening and not self.sent and now - self.since >= self.skip_s

    def take(self) -> bool:
        """A final transcript is ready: may it be sent? (Only the first one per question.)"""
        with self._lock:
            if not self.listening or self.sent:
                return False
            self.sent = True
            return True


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mqtt", default="carebot.local", help="the robot's MQTT broker (default carebot.local)")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--unit", default="robot-4A")
    ap.add_argument("--device", help="microphone name or index (default: the system default mic)")
    ap.add_argument("--model", default="models/vosk-model-small-en-us-0.15")
    ap.add_argument("--list", action="store_true", help="list microphones, then exit")
    args = ap.parse_args()

    if args.list:
        from carebot.voice.devices import list_audio
        list_audio()
        return

    from carebot.net.transport import MqttTransport
    from carebot.voice.listen import VoskMic, _clean

    mic = VoskMic({"vosk_model": args.model, "input_device": args.device})
    mic.open()
    mic.capturing.set()  # the gate below decides what is used
    gate = Gate()

    def on_event(msg: dict) -> None:
        if msg.get("type") == "listening":
            on = bool(msg.get("on"))
            gate.set(on, time.monotonic())
            print("\n>>> Robot is listening: speak now" if on else "    (robot stopped listening)", flush=True)

    transport = MqttTransport(args.mqtt, args.port, args.unit, client_id="carebot-remote-mic")
    transport.subscribe("event", on_event)
    print(f"Laptop mic for {args.unit} via {args.mqtt}:{args.port}. Sends text only, and only while the "
          "robot is listening. Ctrl+C to stop.", flush=True)

    rec, gen = None, -1
    try:
        while True:
            try:
                chunk = mic.audio.get(timeout=0.1)
            except queue.Empty:
                continue
            now = time.monotonic()
            if not gate.hearing(now):
                continue
            if gate.generation != gen:  # a new question: forget what was heard before
                rec, gen = mic.recognizer("transcript"), gate.generation
            if rec.AcceptWaveform(chunk):
                text = _clean(json.loads(rec.Result()).get("text", ""))
                if text and gate.take():
                    transport.publish("cmd", {"type": "heard", "text": text})
                    print(f'    sent: "{text}"', flush=True)
                elif text:
                    print(f'    heard but not sent (robot not listening): "{text}"', flush=True)
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
