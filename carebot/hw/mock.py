"""Laptop stand-ins for the hardware. Same behavior as the Pi drivers, printed to the terminal.

Owned by Software. If a Pi driver and this file disagree, the contract in base.py wins.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from carebot.hw.base import EYE_STATES, Button, Buzzer, Eyes, EyeState, PanTilt

RED = "\033[1;41;97m"
RESET = "\033[0m"


def _out(enabled: bool, msg: str) -> None:
    if enabled:
        print(msg, flush=True)


class MockPanTilt(PanTilt):
    def __init__(self, cfg: dict | None = None) -> None:
        cfg = cfg or {}
        self._pan_limit = tuple(cfg.get("pan", {}).get("limit_deg", (-60, 60)))  # matches hardware_pi.yaml
        self._tilt_limit = tuple(cfg.get("tilt", {}).get("limit_deg", (-30, 45)))
        self._home = tuple(cfg.get("home_deg", (0, 0)))
        self._settle_s = cfg.get("settle_ms", 250) / 1000
        self._verbose = cfg.get("verbose", True)
        self._lock = threading.Lock()
        self._pos = (float(self._home[0]), float(self._home[1]))
        self._moved_at = -1e9
        self._closed = False

    def point(self, pan_deg: float, tilt_deg: float) -> None:
        pan = min(max(pan_deg, self._pan_limit[0]), self._pan_limit[1])
        tilt = min(max(tilt_deg, self._tilt_limit[0]), self._tilt_limit[1])
        with self._lock:
            if (pan, tilt) == self._pos:
                return
            self._pos = (float(pan), float(tilt))
            self._moved_at = time.monotonic()
        _out(self._verbose, f"[head] pan {pan:+6.1f}  tilt {tilt:+6.1f}")

    def home(self) -> None:
        self.point(*self._home)

    def position(self) -> tuple[float, float]:
        with self._lock:
            return self._pos

    def is_moving(self) -> bool:
        with self._lock:
            return time.monotonic() - self._moved_at < self._settle_s

    def close(self) -> None:
        self._closed = True


class MockEyes(Eyes):
    def __init__(self, cfg: dict | None = None) -> None:
        cfg = cfg or {}
        self._verbose = cfg.get("verbose", True)
        self.state: EyeState = "off"

    def set(self, state: EyeState) -> None:
        if state not in EYE_STATES:
            # Contract: never raise at runtime. Log and show the error look instead.
            _out(self._verbose, f"[eyes] unknown state {state!r}, showing 'error'")
            state = "error"
        if state == self.state:
            return
        self.state = state
        _out(self._verbose, f"[eyes] {state}")

    def close(self) -> None:
        self.state = "off"


class MockBuzzer(Buzzer):
    def __init__(self, cfg: dict | None = None) -> None:
        cfg = cfg or {}
        self._verbose = cfg.get("verbose", True)
        self.alarm_on = False
        self.chirps = 0

    def chirp(self) -> None:
        self.chirps += 1
        _out(self._verbose, "[buzzer] chirp")

    def start_alarm(self) -> None:
        if self.alarm_on:
            return
        self.alarm_on = True
        _out(self._verbose, f"{RED}  ALARM: CareBot is beeping. Press the button (type b + Enter).  {RESET}")

    def stop_alarm(self) -> None:
        if not self.alarm_on:
            return
        self.alarm_on = False
        _out(self._verbose, "[buzzer] alarm stopped")

    def close(self) -> None:
        self.alarm_on = False


class MockButton(Button):
    """Presses come from `press()`. The terminal console calls it when you type `b` + Enter."""

    def __init__(self, cfg: dict | None = None) -> None:
        self._callback: Callable[[float], None] | None = None

    def on_press(self, callback: Callable[[float], None]) -> None:
        self._callback = callback

    def press(self) -> None:
        if self._callback is not None:
            self._callback(time.monotonic())

    def close(self) -> None:
        self._callback = None
