"""Pi 5 driver for the LED eyes: a WS2812B (NeoPixel) ring or arc on SPI0 MOSI, GPIO 10.

Owned by Hardware. Implements carebot.hw.base.Eyes; see docs/HARDWARE.md and docs/wiring.md.
Uses adafruit-circuitpython-neopixel-spi (rpi_ws281x doesn't run on the Pi 5) and needs
`dtparam=spi=on` in /boot/firmware/config.txt. Animations run on the driver's own thread.

The looks (docs/HARDWARE.md, "Eye states") live in `render()`, a pure function tested on a laptop.
"""

from __future__ import annotations

import logging
import math
import threading
import time

from carebot.hw.base import EYE_STATES, Eyes, EyeState

log = logging.getLogger(__name__)

FPS = 30
RGB = tuple[int, int, int]

WHITE: RGB = (255, 255, 255)
BLUE: RGB = (0, 90, 255)
AMBER: RGB = (255, 120, 0)
RED: RGB = (255, 0, 0)
GREEN: RGB = (0, 255, 40)
PURPLE: RGB = (140, 0, 255)
OFF: RGB = (0, 0, 0)


def _scale(color: RGB, level: float) -> RGB:
    level = min(max(level, 0.0), 1.0)
    return (round(color[0] * level), round(color[1] * level), round(color[2] * level))


def _wave(t: float, period_s: float) -> float:
    """0 -> 1 -> 0 smoothly once per period."""
    return 0.5 - 0.5 * math.cos(2 * math.pi * t / period_s)


def render(state: str, t: float, n: int, max_brightness: float = 1.0) -> list[RGB]:
    """Colors for n pixels, t seconds after `state` was set. Unknown states show `error`."""
    if state == "off":
        frame = [OFF] * n
    elif state == "boot":  # white chase around the arc, one lap per second, with a short tail
        head = int(t * n) % n if n else 0
        frame = [OFF] * n
        for back, level in ((0, 1.0), (1, 0.4), (2, 0.15)):
            if n > back:
                frame[(head - back) % n] = _scale(WHITE, level)
    elif state == "watching":  # soft blue breathing, about a 4 s cycle
        frame = [_scale(BLUE, 0.15 + 0.85 * _wave(t, 4.0))] * n
    elif state == "checking":  # amber, a steady pulse about once a second
        frame = [_scale(AMBER, 0.25 + 0.75 * _wave(t, 1.0))] * n
    elif state == "alerted":  # red, fast flash about twice a second
        frame = [RED if (t % 0.5) < 0.25 else OFF] * n
    elif state == "help_coming":  # steady green
        frame = [GREEN] * n
    else:  # "error" and anything unknown: dim purple, steady
        frame = [_scale(PURPLE, 0.3)] * n
    return [_scale(c, max_brightness) for c in frame]


class PiEyes(Eyes):
    def __init__(self, cfg: dict) -> None:
        import board  # lazy: laptops never need Blinka
        import neopixel_spi

        if int(cfg.get("spi_bus", 0)) != 0:
            raise ValueError("eyes: only SPI0 (MOSI on GPIO 10) is supported")
        self._n = int(cfg.get("num_pixels", 12))
        self._max = float(cfg.get("max_brightness", 0.3))
        order = getattr(neopixel_spi, str(cfg.get("pixel_order", "GRB")).upper())
        self._spi = board.SPI()
        # Brightness is applied in render(), so the library runs at full scale.
        self._pixels = neopixel_spi.NeoPixel_SPI(self._spi, self._n, pixel_order=order,
                                                  auto_write=False, brightness=1.0)
        self._lock = threading.Lock()
        self._state: str = "off"
        self._since = time.monotonic()
        self._stop = threading.Event()
        self._write([OFF] * self._n)
        self._thread = threading.Thread(target=self._run, daemon=True, name="eyes")
        self._thread.start()

    # The contract (carebot/hw/base.py)

    def set(self, state: EyeState) -> None:
        if state not in EYE_STATES:
            log.warning("eyes: unknown state %r, showing 'error'", state)
            state = "error"
        with self._lock:
            if state == self._state:
                return
            self._state, self._since = state, time.monotonic()

    def close(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        self._thread.join(timeout=1)
        self._write([OFF] * self._n)
        try:
            self._spi.deinit()
        except Exception:
            log.exception("eyes: releasing SPI failed")

    # Helpers

    def _run(self) -> None:
        failed = False
        while not self._stop.wait(1 / FPS):
            with self._lock:
                state, t = self._state, time.monotonic() - self._since
            ok = self._write(render(state, t, self._n, self._max))
            if not ok and not failed:
                log.error("eyes: writing the LEDs failed; will keep trying quietly")
            failed = not ok

    def _write(self, colors: list[RGB]) -> bool:
        try:
            for i, c in enumerate(colors):
                self._pixels[i] = c
            self._pixels.show()
            return True
        except Exception:
            log.debug("eyes: write failed", exc_info=True)
            return False
