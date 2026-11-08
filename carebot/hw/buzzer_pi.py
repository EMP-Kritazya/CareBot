"""Pi 5 driver for the buzzer (gpiozero).

Owned by Hardware. Implements carebot.hw.base.Buzzer; see docs/HARDWARE.md and docs/wiring.md.

`buzzer.type` in config/hardware_pi.yaml:
  active   beeps by itself when its pin is high (often has a sticker on top): driven on/off
  passive  needs a tone: driven with a square wave at `passive_hz`
A bare buzzer draws more than a GPIO pin should give, so it goes through a transistor (see
docs/wiring.md). Three-pin buzzer modules (VCC, GND, signal) connect to the GPIO directly.
"""

from __future__ import annotations

import logging
import threading
import time

from carebot.hw.base import Buzzer

log = logging.getLogger(__name__)

CHIRP_S = 0.12


class PiBuzzer(Buzzer):
    def __init__(self, cfg: dict) -> None:
        from gpiozero import DigitalOutputDevice, PWMOutputDevice  # lazy: laptops never need it

        pin = int(cfg.get("gpio", 17))
        self._passive = str(cfg.get("type", "active")).lower() == "passive"
        self._on_s = float(cfg.get("alarm_on_ms", 400)) / 1000
        self._off_s = float(cfg.get("alarm_off_ms", 300)) / 1000
        if self._passive:
            self._dev = PWMOutputDevice(pin, frequency=float(cfg.get("passive_hz", 2800)), initial_value=0)
        else:
            self._dev = DigitalOutputDevice(pin, initial_value=False)
        self._lock = threading.Lock()
        self._alarm_stop: threading.Event | None = None
        self._chirp_until = 0.0
        self._closed = False

    # The contract (carebot/hw/base.py)

    def chirp(self) -> None:
        with self._lock:
            if self._closed or self._alarm_stop is not None:
                return  # the alarm is already louder than a chirp
            self._chirp_until = time.monotonic() + CHIRP_S
            self._sound(True)
        timer = threading.Timer(CHIRP_S, self._end_chirp)
        timer.daemon = True
        timer.start()

    def start_alarm(self) -> None:
        with self._lock:
            if self._closed or self._alarm_stop is not None:
                return
            stop = self._alarm_stop = threading.Event()
        threading.Thread(target=self._alarm_loop, args=(stop,), daemon=True, name="buzzer-alarm").start()

    def stop_alarm(self) -> None:
        with self._lock:
            if self._alarm_stop is None:
                return
            self._alarm_stop.set()
            self._alarm_stop = None
            self._sound(False)

    def close(self) -> None:
        self.stop_alarm()
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._sound(False)
            try:
                self._dev.close()
            except Exception:
                log.exception("buzzer: closing the pin failed")

    # Helpers (call with the lock held)

    def _sound(self, on: bool) -> None:
        try:
            if self._passive:
                self._dev.value = 0.5 if on else 0
            elif on:
                self._dev.on()
            else:
                self._dev.off()
        except Exception:
            log.exception("buzzer: pin write failed")

    def _end_chirp(self) -> None:
        with self._lock:
            if not self._closed and self._alarm_stop is None and time.monotonic() >= self._chirp_until - 0.01:
                self._sound(False)

    def _alarm_loop(self, stop: threading.Event) -> None:
        while not stop.is_set():
            with self._lock:
                if stop.is_set():
                    return
                self._sound(True)
            if stop.wait(self._on_s):
                return
            with self._lock:
                if stop.is_set():
                    return
                self._sound(False)
            stop.wait(self._off_s)
