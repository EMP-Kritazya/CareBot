"""Pi 5 driver for the pan-tilt head: two hobby servos on hardware PWM (rpi-hardware-pwm).

Owned by Hardware. Implements carebot.hw.base.PanTilt; see docs/HARDWARE.md and docs/wiring.md.

Angle to pulse, per axis (all numbers from config/hardware_pi.yaml):

    pulse_us = center_us + direction * (deg + center_trim_deg) * us_per_deg

clamped to [min_us, max_us]. direction is -1 when `invert` is true. Calibrate the numbers with
`python -m apps.hwcheck pantilt`.
"""

from __future__ import annotations

import logging
import math
import threading
import time

from carebot.hw.base import PanTilt

log = logging.getLogger(__name__)

AXES = ("pan", "tilt")
RAW_US_LIMITS = (500.0, 2500.0)  # hard bounds for raw calibration pulses (hwcheck only)


class Axis:
    """Angle-to-pulse math for one servo. Pure, no hardware, so it is tested on a laptop."""

    def __init__(self, cfg: dict) -> None:
        self.channel = int(cfg["channel"])
        self.min_us = float(cfg.get("min_us", 1000))
        self.max_us = float(cfg.get("max_us", 2000))
        if self.min_us >= self.max_us:
            raise ValueError(f"min_us ({self.min_us}) must be below max_us ({self.max_us})")
        self.center_us = float(cfg.get("center_us", (self.min_us + self.max_us) / 2))
        # Without us_per_deg, assume the pulse range spans 180 degrees (true for SG90s).
        self.us_per_deg = float(cfg.get("us_per_deg", (self.max_us - self.min_us) / 180))
        if self.us_per_deg <= 0:
            raise ValueError("us_per_deg must be positive")
        self.trim = float(cfg.get("center_trim_deg", 0))
        self.direction = -1.0 if cfg.get("invert", False) else 1.0
        lo, hi = (float(v) for v in cfg.get("limit_deg", (-90, 90)))
        # Never accept an angle the pulse range can't reach, so position() stays truthful.
        reach_lo, reach_hi = sorted((self.deg_for(self.min_us), self.deg_for(self.max_us)))
        self.lo, self.hi = max(lo, reach_lo), min(hi, reach_hi)
        if self.lo > self.hi:
            raise ValueError(f"limit_deg {lo, hi} is outside the reachable range {reach_lo:.1f}..{reach_hi:.1f}")

    def deg_for(self, us: float) -> float:
        """The angle a pulse width means (inverse of pulse_us, ignoring clamping)."""
        return (us - self.center_us) / (self.direction * self.us_per_deg) - self.trim

    def clamp(self, deg: float) -> float:
        return min(max(float(deg), self.lo), self.hi)

    def pulse_us(self, deg: float) -> float:
        us = self.center_us + self.direction * (self.clamp(deg) + self.trim) * self.us_per_deg
        return min(max(us, self.min_us), self.max_us)


class PiPanTilt(PanTilt):
    """Two servos on the Pi 5's PWM0: pan on channel 0 (GPIO 12), tilt on channel 1 (GPIO 13).

    Needs `dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4` in /boot/firmware/config.txt.
    The servos hold the home pose from start until close(), which stops PWM (servos go limp).
    """

    def __init__(self, cfg: dict) -> None:
        from rpi_hardware_pwm import HardwarePWM  # lazy: laptops never need it

        self._axes = {name: Axis(cfg[name]) for name in AXES}
        self._hz = float(cfg.get("hz", 50))
        self._period_us = 1e6 / self._hz
        self._settle_s = float(cfg.get("settle_ms", 250)) / 1000
        home = cfg.get("home_deg", (0, 0))
        self._home = (self._axes["pan"].clamp(home[0]), self._axes["tilt"].clamp(home[1]))
        self._lock = threading.Lock()
        self._pwms: dict = {}
        self._closed = False

        chip = int(cfg.get("pwm_chip", 0))
        try:
            for name, deg in zip(AXES, self._home):
                axis = self._axes[name]
                pwm = HardwarePWM(pwm_channel=axis.channel, hz=self._hz, chip=chip)
                pwm.start(self._duty(axis.pulse_us(deg)))
                self._pwms[name] = pwm
        except Exception as e:
            self._stop_all()
            raise RuntimeError(
                f"pan-tilt PWM failed to start on pwmchip{chip}: {e}. Check that the pwm-2chan overlay "
                "is in /boot/firmware/config.txt and that pantilt.pwm_chip matches `ls /sys/class/pwm`."
            ) from e

        self._pos = self._home
        self._moved_at = time.monotonic()

    # The contract (carebot/hw/base.py)

    def point(self, pan_deg: float, tilt_deg: float) -> None:
        try:
            pan_deg, tilt_deg = float(pan_deg), float(tilt_deg)
        except (TypeError, ValueError):
            log.warning("pan-tilt: ignoring non-numeric point(%r, %r)", pan_deg, tilt_deg)
            return
        if not (math.isfinite(pan_deg) and math.isfinite(tilt_deg)):
            log.warning("pan-tilt: ignoring non-finite point(%r, %r)", pan_deg, tilt_deg)
            return
        target = (self._axes["pan"].clamp(pan_deg), self._axes["tilt"].clamp(tilt_deg))
        with self._lock:
            if self._closed or target == self._pos:
                return
            try:
                for name, old, new in zip(AXES, self._pos, target):
                    if new != old:
                        self._pwms[name].change_duty_cycle(self._duty(self._axes[name].pulse_us(new)))
            except Exception:
                log.exception("pan-tilt: PWM write failed")
                return
            self._pos = target
            self._moved_at = time.monotonic()

    def home(self) -> None:
        self.point(*self._home)

    def position(self) -> tuple[float, float]:
        with self._lock:
            return self._pos

    def is_moving(self) -> bool:
        with self._lock:
            return time.monotonic() - self._moved_at < self._settle_s

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._stop_all()

    # Calibration only (apps.hwcheck). Not part of the contract; the app never calls this.

    def raw_pulse(self, axis: str, us: float) -> None:
        """Send one axis a raw pulse width, ignoring the config limits (hard-bounded to 500..2500 us).
        position() is not updated, so call home() or point() afterwards."""
        us = min(max(float(us), RAW_US_LIMITS[0]), RAW_US_LIMITS[1])
        with self._lock:
            if self._closed:
                return
            try:
                self._pwms[axis].change_duty_cycle(self._duty(us))
            except Exception:
                log.exception("pan-tilt: raw pulse failed")
                return
            self._pos = (math.nan, math.nan)  # unknown until the next point()/home()
            self._moved_at = time.monotonic()

    # Helpers

    def _duty(self, us: float) -> float:
        return us / self._period_us * 100

    def _stop_all(self) -> None:
        for name, pwm in list(self._pwms.items()):
            try:
                pwm.stop()
            except Exception:
                log.exception("pan-tilt: stopping %s PWM failed", name)
        self._pwms.clear()
