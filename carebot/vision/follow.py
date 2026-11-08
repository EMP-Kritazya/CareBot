"""Head follow: turn the pan-tilt head so the resident stays in frame. Pure, unit tested.

Design:
- The head **pans** to follow Sandy. Panning shifts the image sideways only; falls are detected from
  vertical hip movement and torso angle, so panning doesn't blind the fall detector.
- The **tilt** is off by default: falls are measured from vertical hip movement, and tilting during a
  fall could hide it. With `tilt_enabled`:
  - **down** only after he has been low in the frame for `tilt_dwell_s` (longer than a fall's drop
    window, 1.2 s). A real fall reaches the fall detector first, and that freezes the head; sitting
    down is followed a moment later.
  - **up** when his head nears the top edge (`tilt_headroom`) or his torso is high, after the short
    `tilt_up_dwell_s`. Rising can't hide a fall, so it doesn't need the long wait. It looks at the
    head, not the torso middle: otherwise the head leaves the frame before the torso is off-center.
  Frames while the tilt moves or settles are skipped by the fall detector (`Frame.tilt_moving`).
- The head **freezes** during a possible fall, a check-in or an alert, and while he's lying down:
  CareBot keeps watching the spot where he is.
- If he's out of view for `home_after_s`, the head goes home and waits (field-of-view rule).

Angles follow the PanTilt contract: pan_deg > 0 turns toward the right of the image, tilt_deg > 0 up.
"""

from __future__ import annotations

from dataclasses import dataclass

from carebot.vision.keypoints import L_EAR, L_EYE, L_HIP, L_SHOULDER, NOSE, R_EAR, R_EYE, R_HIP, R_SHOULDER, Person

HEAD = (NOSE, L_EYE, R_EYE, L_EAR, R_EAR)


def target_point(person: Person, min_conf: float = 0.3) -> tuple[float, float] | None:
    """Where to aim: the middle of the torso (shoulders + hips), or of whatever is visible."""
    torso = [person.keypoints[i] for i in (L_SHOULDER, R_SHOULDER, L_HIP, R_HIP) if person.keypoints[i][2] >= min_conf]
    pts = torso or [k for k in person.keypoints if k[2] >= min_conf]
    if not pts:
        return None
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


def head_top(person: Person, min_conf: float = 0.3) -> float | None:
    """How high his head is in the frame (0 = top edge). Head hidden: estimated from the shoulders
    (just above them); shoulders hidden too but hips seen high: his head is above the frame (0)."""
    k = person.keypoints
    head = [k[i][1] for i in HEAD if k[i][2] >= min_conf]
    if head:
        return min(head)
    shoulders = [k[i][1] for i in (L_SHOULDER, R_SHOULDER) if k[i][2] >= min_conf]
    if shoulders:
        return max(0.0, min(shoulders) - 0.08)
    hips = [k[i][1] for i in (L_HIP, R_HIP) if k[i][2] >= min_conf]
    if hips and min(hips) < 0.5:
        return 0.0
    return None


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
    tilt_dwell_s: float = 1.5     # low in the frame this long before tilting down (> fall window)
    tilt_up_dwell_s: float = 0.4  # head near the top this long before tilting up (rising can't hide a fall)
    tilt_headroom: float = 0.10   # keep the top of his head at least this far below the top edge
    tilt_max_step_deg: float = 2.0
    _last_cmd: float = -1e9
    _down_since: float | None = None
    _up_since: float | None = None
    _last_seen: float | None = None
    _homed: bool = True

    @classmethod
    def from_config(cls, cfg: dict) -> "Follower":
        keys = ("deadband", "gain", "max_step_deg", "rate_hz", "home_after_s", "tilt_enabled", "tilt_deadband",
                "tilt_dwell_s", "tilt_up_dwell_s", "tilt_headroom", "tilt_max_step_deg")
        f = cls(**{k: cfg[k] for k in keys if k in cfg})
        if "fov_deg" in cfg:
            f.fov_deg = tuple(cfg["fov_deg"])
        return f

    def vertical_need(self, target: tuple[float, float] | None, top: float | None) -> int:
        """+1 = he needs the head to tilt up, -1 = down, 0 = fine. Up (head near the top) wins."""
        if target is None:
            return 0
        if (top is not None and top < self.tilt_headroom) or 0.5 - target[1] > self.tilt_deadband:
            return 1
        if target[1] - 0.5 > self.tilt_deadband:
            return -1
        return 0

    def update(self, ts: float, target: tuple[float, float] | None, position: tuple[float, float],
               frozen: bool, mirrored: bool = False, top: float | None = None):
        """Returns ("point", pan, tilt), ("home",), or None (don't move).
        `top`: how high his head is (head_top), for tilting up before it leaves the frame."""
        if target is not None:
            self._last_seen = ts
        need = 0 if frozen else self.vertical_need(target, top)
        self._up_since = (self._up_since if self._up_since is not None else ts) if need == 1 else None
        self._down_since = (self._down_since if self._down_since is not None else ts) if need == -1 else None
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
        new_pan, new_tilt = pan, tilt
        if abs(ex) > self.deadband:
            new_pan = pan + _clamp(self.gain * ex * self.fov_deg[0], self.max_step_deg)
        if self.tilt_enabled:
            if self._up_since is not None and ts - self._up_since >= self.tilt_up_dwell_s:
                new_tilt = tilt + self.tilt_max_step_deg
            elif self._down_since is not None and ts - self._down_since >= self.tilt_dwell_s:
                new_tilt = tilt - self.tilt_max_step_deg
        if (new_pan, new_tilt) == (pan, tilt):
            return None
        self._last_cmd = ts
        self._homed = False
        return ("point", new_pan, new_tilt)


def _clamp(v: float, limit: float) -> float:
    return max(-limit, min(limit, v))
