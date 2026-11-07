"""Synthetic COCO-17 skeletons for tests and debugging. Not used on the robot."""

from __future__ import annotations

import math
import random

from carebot.vision.keypoints import Frame, Keypoint, Person


def person(track_id: int, hip: tuple[float, float], posture: str = "upright", torso_deg: float | None = None,
           torso_len: float = 0.25, aspect: float = 4 / 3, conf: float = 0.9, lean: int = 1,
           jitter: float = 0.0, rng: random.Random | None = None) -> Person:
    """A plausible skeleton. `torso_deg` defaults to 5 (upright/sitting) or 90 (lying)."""
    if torso_deg is None:
        torso_deg = 90.0 if posture == "lying" else 5.0
    L = torso_len
    th = math.radians(torso_deg)
    dx, dy = lean * L * math.sin(th) / aspect, -L * math.cos(th)  # hip -> shoulder, normalized
    hx, hy = hip
    sx, sy = hx + dx, hy + dy
    w = 0.08 * L / aspect
    head = (sx + 0.35 * dx, sy + 0.35 * dy)
    if posture == "lying":
        knee = (hx - 0.5 * dx, hy - 0.5 * dy)
        ankle = (hx - 1.0 * dx, hy - 1.0 * dy)
    elif posture == "sitting":
        knee = (hx + lean * 0.45 * L / aspect, hy + 0.05 * L)
        ankle = (knee[0], knee[1] + 0.45 * L)
    else:
        knee = (hx, hy + 0.45 * L)
        ankle = (hx, hy + 0.9 * L)
    elbow = (sx - 0.4 * dx, sy - 0.4 * dy)
    wrist = (sx - 0.8 * dx, sy - 0.8 * dy)
    pts = [
        head, (head[0] - w / 2, head[1]), (head[0] + w / 2, head[1]), (head[0] - w, head[1]), (head[0] + w, head[1]),
        (sx - w, sy), (sx + w, sy), (elbow[0] - w, elbow[1]), (elbow[0] + w, elbow[1]),
        (wrist[0] - w, wrist[1]), (wrist[0] + w, wrist[1]),
        (hx - w, hy), (hx + w, hy), (knee[0] - w, knee[1]), (knee[0] + w, knee[1]),
        (ankle[0] - w, ankle[1]), (ankle[0] + w, ankle[1]),
    ]
    r = rng or random.Random(track_id)
    kps: list[Keypoint] = [(x + r.uniform(-jitter, jitter), y + r.uniform(-jitter, jitter), conf) for x, y in pts]
    return Person(track_id, tuple(kps))


def frame(ts: float, *people: Person, resident: int | None = None, moving: bool = False,
          aspect: float = 4 / 3) -> Frame:
    return Frame(ts=ts, people=tuple(people), resident_track_id=resident, camera_moving=moving, aspect=aspect)


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * max(0.0, min(1.0, t))
