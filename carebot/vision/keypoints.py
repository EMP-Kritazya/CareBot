"""Shared pose types and per-person features. Pure Python, no numpy, no camera.

Coordinates are normalized to the frame: x in [0, 1] of width, y in [0, 1] of height, y grows
downward. Angles and lengths are corrected for the frame's aspect ratio, so a person's torso angle
doesn't change with the camera's resolution.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

# COCO-17 keypoint indices (same order as YOLO pose and the old imx500 models).
NOSE, L_EYE, R_EYE, L_EAR, R_EAR = 0, 1, 2, 3, 4
L_SHOULDER, R_SHOULDER, L_ELBOW, R_ELBOW, L_WRIST, R_WRIST = 5, 6, 7, 8, 9, 10
L_HIP, R_HIP, L_KNEE, R_KNEE, L_ANKLE, R_ANKLE = 11, 12, 13, 14, 15, 16
NUM_KEYPOINTS = 17

Keypoint = tuple[float, float, float]  # x, y, confidence
Posture = Literal["upright", "sitting", "lying", "unknown"]


@dataclass(frozen=True)
class Person:
    track_id: int
    keypoints: tuple[Keypoint, ...]  # 17 entries


@dataclass(frozen=True)
class Frame:
    ts: float                          # seconds (monotonic)
    people: tuple[Person, ...]
    resident_track_id: int | None = None  # decided by the face lock
    camera_moving: bool = False        # pan-tilt still settling: skip motion-based logic
    aspect: float = 4 / 3              # frame width / height

    def person(self, track_id: int | None) -> Person | None:
        if track_id is None:
            return None
        return next((p for p in self.people if p.track_id == track_id), None)


def _mid(kps: tuple[Keypoint, ...], a: int, b: int, min_conf: float) -> tuple[float, float] | None:
    pa, pb = kps[a], kps[b]
    ok_a, ok_b = pa[2] >= min_conf, pb[2] >= min_conf
    if ok_a and ok_b:
        return ((pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2)
    if ok_a:
        return (pa[0], pa[1])
    if ok_b:
        return (pb[0], pb[1])
    return None


@dataclass(frozen=True)
class Features:
    hip: tuple[float, float]       # hip midpoint
    torso_deg: float               # 0 = upright, 90 = horizontal, >90 = head below hips
    torso_len: float               # shoulder-mid to hip-mid, in frame-height units
    posture: Posture


def features(person: Person, aspect: float, min_conf: float = 0.3,
             lying_deg: float = 60, sitting_knee_ratio: float = 0.35) -> Features | None:
    """None if the hips or shoulders aren't visible enough to judge."""
    kps = person.keypoints
    hip = _mid(kps, L_HIP, R_HIP, min_conf)
    shoulder = _mid(kps, L_SHOULDER, R_SHOULDER, min_conf)
    if hip is None or shoulder is None:
        return None
    vx, vy = (shoulder[0] - hip[0]) * aspect, shoulder[1] - hip[1]
    torso_len = math.hypot(vx, vy)
    if torso_len < 1e-6:
        return None
    torso_deg = math.degrees(math.atan2(abs(vx), -vy))
    if torso_deg >= lying_deg:
        posture: Posture = "lying"
    else:
        knee = _mid(kps, L_KNEE, R_KNEE, min_conf)
        if knee is None:
            posture = "upright"
        else:
            # Sitting: knees are roughly level with the hips (thighs horizontal).
            posture = "sitting" if (knee[1] - hip[1]) < sitting_knee_ratio * torso_len else "upright"
    return Features(hip=hip, torso_deg=torso_deg, torso_len=torso_len, posture=posture)


def body_view(person: Person, aspect: float, min_conf: float = 0.3, lying_deg: float = 60,
              sitting_knee_ratio: float = 0.35) -> tuple[str, float] | None:
    """(posture, body scale in frame-height units) for stillness, even when the hips are hidden.

    Full body: posture from `features`, scale = torso length.
    Hips hidden (behind a desk, a blanket, the chair arm): if both shoulders and the head are visible
    and the head is above the shoulders, posture = "upper_body" (sitting or standing, seen from the
    chest up) and scale = 1.25 x shoulder width, which is about one torso length on an adult.
    """
    feat = features(person, aspect, min_conf, lying_deg, sitting_knee_ratio)
    if feat is not None:
        return feat.posture, feat.torso_len
    kps = person.keypoints
    ls, rs = kps[L_SHOULDER], kps[R_SHOULDER]
    if ls[2] < min_conf or rs[2] < min_conf:
        return None
    head = next((kps[i] for i in (NOSE, L_EYE, R_EYE) if kps[i][2] >= min_conf), None)
    if head is None:
        return None
    width = math.hypot((ls[0] - rs[0]) * aspect, ls[1] - rs[1])
    if width < 1e-6:
        return None
    shoulder_y = (ls[1] + rs[1]) / 2
    if head[1] < shoulder_y - 0.25 * width:
        return "upper_body", 1.25 * width
    return None


def motion(a: Person, b: Person, aspect: float, torso_len: float, min_conf: float = 0.3) -> float | None:
    """Median keypoint displacement between two poses of the same person, in torso lengths.
    The median ignores a single jittery keypoint. None if too few keypoints are visible in both."""
    dists = []
    for (ax, ay, ac), (bx, by, bc) in zip(a.keypoints, b.keypoints):
        if ac >= min_conf and bc >= min_conf:
            dists.append(math.hypot((bx - ax) * aspect, by - ay))
    if len(dists) < 5 or torso_len <= 0:
        return None
    dists.sort()
    return dists[len(dists) // 2] / torso_len
