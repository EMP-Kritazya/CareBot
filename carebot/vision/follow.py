"""Head follow: turn the pan-tilt head so the resident stays in frame. Pure, unit tested.

Design:
- The head **pans** to follow Sandy. Panning shifts the image sideways only; falls are detected from
  vertical hip movement and torso angle, so panning doesn't blind the fall detector.
- The **tilt** stays where it was installed (pointed so the floor is in view). Tilting would corrupt
  the vertical measurements falls depend on. It can be enabled in config for a slow correction only.
- The head **freezes** during a possible fall, a check-in or an alert, and while he's lying down:
  CareBot keeps watching the spot where he is.
- If he's out of view for `home_after_s`, the head goes home and waits (field-of-view rule).

Angles follow the PanTilt contract: pan_deg > 0 turns toward the right of the image, tilt_deg > 0 up.
"""

from __future__ import annotations

from dataclasses import dataclass

from carebot.vision.keypoints import L_HIP, L_SHOULDER, R_HIP, R_SHOULDER, Person


def target_point(person: Person, min_conf: float = 0.3) -> tuple[float, float] | None:
    """Where to aim: the middle of the torso (shoulders + hips), or of whatever is visible."""
    torso = [person.keypoints[i] for i in (L_SHOULDER, R_SHOULDER, L_HIP, R_HIP) if person.keypoints[i][2] >= min_conf]
    pts = torso or [k for k in person.keypoints if k[2] >= min_conf]
    if not pts:
        return None
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


@dataclass
class Follower:
    fov_deg: tuple[float, float] = (66.0, 41.0)   # camera field of view (laptop default; the robot's Logitech: config/pi.yaml)
    deadband: float = 0.12        # no pan while he's within this far of the center (fraction of width)
    gain: float = 0.5             # move this share of the error per update
    max_step_deg: float = 4.0     # never move more than this per update (smooth, no lurching)
    rate_hz: float = 8.0          # at most this many commands per second
    home_after_s: float = 3.0     # out of view this long: go home and wait
    tilt_enabled: bool = False    # tilt corrections off by default (see module docstring)
    tilt_deadband: float = 0.25
    _last_cmd: float = -1e9
    _last_seen: float | None = None
    _homed: bool = True

    @classmethod
    def from_config(cls, cfg: dict) -> "Follower":
        keys = ("deadband", "gain", "max_step_deg", "rate_hz", "home_after_s", "tilt_enabled", "tilt_deadband")
        f = cls(**{k: cfg[k] for k in keys if k in cfg})
        if "fov_deg" in cfg:
            f.fov_deg = tuple(cfg["fov_deg"])
        return f

    def update(self, ts: float, target: tuple[float, float] | None, position: tuple[float, float],
               frozen: bool, mirrored: bool = False):
        """Returns ("point", pan, tilt), ("home",), or None (don't move)."""
        if target is not None:
            self._last_seen = ts
        if frozen:
            return None
        if target is None:
            if not self._homed and self._last_seen is not None and ts - self._last_seen >= self.home_after_s:
                self._homed = True
                return ("home",)
            return None
        if ts - self._last_cmd < 1.0 / self.rate_hz:
            return None
        pan, tilt = position
        ex = (target[0] - 0.5) * (-1 if mirrored else 1)  # + = he's right of center in camera terms
        ey = 0.5 - target[1]                               # + = he's above center
        new_pan, new_tilt = pan, tilt
        if abs(ex) > self.deadband:
            new_pan = pan + _clamp(self.gain * ex * self.fov_deg[0], self.max_step_deg)
        if self.tilt_enabled and abs(ey) > self.tilt_deadband:
            new_tilt = tilt + _clamp(self.gain * ey * self.fov_deg[1], self.max_step_deg)
        if (new_pan, new_tilt) == (pan, tilt):
            return None
        self._last_cmd = ts
        self._homed = False
        return ("point", new_pan, new_tilt)


def _clamp(v: float, limit: float) -> float:
    return max(-limit, min(limit, v))
