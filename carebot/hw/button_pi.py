"""Pi 5 driver for the one big button (gpiozero, which uses lgpio on Bookworm).

Owned by Hardware. Implements carebot.hw.base.Button; see docs/HARDWARE.md and docs/wiring.md.

Two kinds of button work, chosen by `button.pull_up` in config/hardware_pi.yaml:
  pull_up: true   a plain push button between the GPIO and GND (the Pi's internal pull-up)
  pull_up: false  a module that outputs 3.3 V when pressed, e.g. a Grove button (internal pull-down)
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from carebot.hw.base import Button

log = logging.getLogger(__name__)


class PiButton(Button):
    def __init__(self, cfg: dict) -> None:
        from gpiozero import Button as GpioButton  # lazy: laptops never need it

        self._bounce_s = float(cfg.get("bounce_ms", 50)) / 1000
        self._lock = threading.Lock()
        self._callback: Callable[[float], None] | None = None
        self._last_press = -1e9
        self._closed = False
        self._button = GpioButton(int(cfg.get("gpio", 27)), pull_up=bool(cfg.get("pull_up", True)),
                                  bounce_time=self._bounce_s or None)
        self._button.when_pressed = self._pressed

    def _pressed(self) -> None:
        now = time.monotonic()
        with self._lock:
            # A second guard on top of gpiozero's debounce: one physical press = one call.
            if self._closed or now - self._last_press < self._bounce_s:
                return
            self._last_press = now
            callback = self._callback
        if callback is not None:
            try:
                callback(now)
            except Exception:
                log.exception("button: the press callback raised")

    def on_press(self, callback: Callable[[float], None]) -> None:
        with self._lock:
            self._callback = callback

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._callback = None
        try:
            self._button.close()
        except Exception:
            log.exception("button: closing the pin failed")
