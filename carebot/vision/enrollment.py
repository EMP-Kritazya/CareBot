"""Saving the resident's face: shared by the live app (press E) and `python -m apps.enroll`.

Two modes:
- "new": start a fresh face for the resident (replaces what was saved).
- "add": recalibrate, adding samples to the saved face (e.g. at the robot's real distance and
  lighting). Only faces that already look somewhat like the saved one are added, so a visitor
  can't be mixed into Ruth's profile by accident.

Faces are saved to data/faces/resident.npz on this machine only (git-ignored).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

MAX_SAMPLES = 64  # newest kept; old samples age out as she's recalibrated


def merge_samples(existing, new, mode: str, cap: int = MAX_SAMPLES):
    """Pure: the samples to save. "new" replaces; "add" appends and keeps the newest `cap`."""
    import numpy as np

    new = np.asarray(new, dtype=np.float32)
    if mode == "new" or existing is None or len(existing) == 0:
        out = new
    else:
        out = np.concatenate([np.asarray(existing, dtype=np.float32), new])
    return out[-cap:]


def save_face(path: Path, name: str, samples, mode: str) -> int:
    """Write the resident's face file. Returns how many samples it now holds."""
    import numpy as np

    existing = None
    if mode == "add" and path.exists():
        existing = np.load(path)["embeddings"]
    merged = merge_samples(existing, samples, mode)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, name=name, embeddings=merged, updated=time.time())
    return len(merged)


@dataclass
class Enroller:
    """Collects face samples frame by frame. Feed it raw camera images with `step`.

    Each capture stores two samples: the face as the camera sees it and as a mirror, so the
    lock works in a mirrored preview and on the robot (not mirrored).
    """
    matcher: object                    # FaceMatcher
    mode: str = "new"                  # "new" | "add"
    target: int = 16                   # samples to collect (two per capture)
    interval_s: float = 0.4
    min_face_px: int = 80              # "new" needs a close, clear face
    min_face_px_add: int = 36          # "add" may be at room distance (that's the point)
    min_score: float = 0.8             # YuNet confidence
    same_person: float = 0.2           # "add": face must be at least this similar to the saved one
    samples: list = field(default_factory=list)
    _last: float = 0.0

    @property
    def done(self) -> bool:
        return len(self.samples) >= self.target

    @property
    def progress(self) -> str:
        return f"{len(self.samples) // 2}/{self.target // 2}"

    def step(self, raw_image):
        """One camera frame in. Returns (mirrored image to show, message, ok) where ok means a
        usable face was in view this frame."""
        import cv2

        mirrored = cv2.flip(raw_image, 1)
        raw_faces = [f for f in self.matcher.detect(raw_image) if f[-1] >= self.min_score]
        faces = [f for f in self.matcher.detect(mirrored) if f[-1] >= self.min_score]
        min_px = self.min_face_px_add if self.mode == "add" else self.min_face_px
        ok = False
        if not faces or not raw_faces:
            msg = "No face found: look at the camera"
        elif len(faces) > 1 or len(raw_faces) > 1:
            msg = "More than one face: only the person being saved should be in view"
        elif faces[0][2] < min_px:
            msg = "Move closer: face too small"
        else:
            sample = self.matcher.embed(mirrored, faces[0]).flatten()
            if self.mode == "add" and self.matcher.ready and self.matcher.similarity(sample.reshape(1, -1)) < self.same_person:
                msg = f"This doesn't look like the saved {self.matcher.name}: not added"
            else:
                ok = True
                msg = f"Hold still, turn your head a little each way... {self.progress}"
                if time.monotonic() - self._last > self.interval_s:
                    self.samples.append(sample)
                    self.samples.append(self.matcher.embed(raw_image, raw_faces[0]).flatten())
                    self._last = time.monotonic()
        for f in faces:
            x, y, w, h = (int(v) for v in f[:4])
            cv2.rectangle(mirrored, (x, y), (x + w, y + h), (80, 200, 80) if ok else (0, 200, 255), 2)
        return mirrored, msg, ok
