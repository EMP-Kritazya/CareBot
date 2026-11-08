"""Face lock: which tracked person is the resident (Sandy).

How it works:
1. Every few frames, for each tracked person, crop the head using their pose keypoints
   (nose, eyes, ears) and look for a face in that crop with YuNet.
2. Compare the face with Sandy's enrolled faces using SFace (cosine similarity).
3. `ResidentLock` binds a match to that person's track id. The lock then rides the track id
   (ByteTrack) while the face is hidden, because falls hide faces.

Cropping per person means a face always belongs to the body it was found on: no guessing which
skeleton a face goes with. Face data never leaves the robot (data/faces/ is git-ignored).

`ResidentLock` and `head_box` are pure and unit tested. `FaceMatcher` needs OpenCV + the models.
"""

from __future__ import annotations

import math
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from carebot.config import ROOT
from carebot.vision.keypoints import Person

MODELS = {
    "yunet": ("face_detection_yunet_2023mar.onnx",
              "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"),
    "sface": ("face_recognition_sface_2021dec.onnx",
              "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx"),
}


def ensure_model(key: str) -> Path:
    """Download an OpenCV Zoo model into models/ once (git-ignored)."""
    name, url = MODELS[key]
    path = ROOT / "models" / name
    if not path.exists() or path.stat().st_size < 10_000:
        path.parent.mkdir(exist_ok=True)
        print(f"downloading {name}...", flush=True)
        urllib.request.urlretrieve(url, path)
    return path


# ---------------------------------------------------------------- pure parts

def head_box(person: Person, width: int, height: int, min_conf: float = 0.3,
             scale: float = 3.0, min_px: int = 40) -> tuple[int, int, int, int] | None:
    """Square pixel box (x1, y1, x2, y2) around the head, from the face keypoints (0-4).
    None if fewer than two face keypoints are visible (head turned away, out of frame)."""
    pts = [(x * width, y * height) for x, y, c in person.keypoints[:5] if c >= min_conf]
    if len(pts) < 2:
        return None
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    spread = max(math.dist(a, b) for i, a in enumerate(pts) for b in pts[i + 1:])
    half = max(min_px, scale * spread) / 2
    x1, y1 = max(0, int(cx - half)), max(0, int(cy - half))
    x2, y2 = min(width, int(cx + half)), min(height, int(cy + half))
    if x2 - x1 < 8 or y2 - y1 < 8:
        return None
    return x1, y1, x2, y2


@dataclass
class ResidentLock:
    """Decides which track id is the resident, from face scores and track continuity.

    - A face must score >= match_threshold on `confirm_hits` checks in a row before the resident is
      bound to that track. One lucky frame can't move "Sandy" onto someone else.
    - If two tracks match in the same check, the best one wins only by `switch_margin`; otherwise the
      current lock stays where it is.
    - A clear face scoring < reject_threshold marks that track as "not the resident": if the lock was
      on it (a tracker id swap), the lock is dropped.
    - With a face enrolled, a track whose face was seen and did NOT reach match_threshold is never
      assumed to be the resident. The "only person in view" assumption is for when no face is
      enrolled yet, or the face simply can't be seen (too far, turned away).
    - The lock survives while its track is visible, and for relock_after_s after it disappears.
    """
    match_threshold: float = 0.42
    reject_threshold: float = 0.30
    confirm_hits: int = 2
    switch_margin: float = 0.08
    relock_after_s: float = 2.0
    assume_single_is_resident: bool = True
    enrolled: bool = True
    track_id: int | None = None
    source: str | None = None          # "face" | "assumed" | "manual"
    score: float | None = None
    missing_since: float | None = None
    not_resident: dict[int, float] = field(default_factory=dict)  # track id -> when rejected
    unmatched: dict[int, float] = field(default_factory=dict)     # face seen but below match -> when
    hits: dict[int, int] = field(default_factory=dict)            # consecutive matching checks

    @classmethod
    def from_config(cls, cfg: dict, enrolled: bool = True) -> "ResidentLock":
        keys = ("match_threshold", "reject_threshold", "confirm_hits", "switch_margin", "relock_after_s",
                "assume_single_is_resident")
        return cls(**{k: cfg[k] for k in keys if k in cfg}, enrolled=enrolled)

    def update(self, ts: float, track_ids: list[int], scores: dict[int, float]) -> int | None:
        visible = set(track_ids)
        for tid, sc in scores.items():
            if sc >= self.match_threshold:
                self.hits[tid] = self.hits.get(tid, 0) + 1
                self.unmatched.pop(tid, None)
                self.not_resident.pop(tid, None)
            else:
                self.hits[tid] = 0
                self.unmatched[tid] = ts
                if sc < self.reject_threshold:
                    self.not_resident[tid] = ts

        confirmed = sorted(((scores[t], t) for t in scores if self.hits.get(t, 0) >= self.confirm_hits), reverse=True)
        if confirmed:
            best_score, best = confirmed[0]
            tie = len(confirmed) > 1 and best_score - confirmed[1][0] < self.switch_margin
            current_ok = self.track_id in scores and scores[self.track_id] >= self.match_threshold
            if not tie and not (current_ok and self.track_id != best and self.source == "face"):
                self.track_id, self.source, self.score, self.missing_since = best, "face", best_score, None
            elif self.track_id in scores:
                self.score = scores[self.track_id]
        if self.track_id is not None and self.source != "manual":
            if self.track_id in self.not_resident:
                self._clear()  # a clear face says this track isn't him (tracker swapped ids)
            elif self.source == "assumed" and self.enrolled and self.track_id in self.unmatched:
                self._clear()  # assumed, but the face we just saw isn't his
            elif self.source == "face" and self.track_id in scores:
                self.score = scores[self.track_id]  # still him, maybe a weaker view

        if self.track_id is not None:
            if self.track_id in visible:
                self.missing_since = None
            else:
                self.missing_since = self.missing_since if self.missing_since is not None else ts
                if ts - self.missing_since > self.relock_after_s:
                    self._clear()

        if self.track_id is None and self.assume_single_is_resident and len(visible) == 1:
            only = next(iter(visible))
            if only not in self.not_resident and not (self.enrolled and only in self.unmatched):
                self.track_id, self.source, self.score, self.missing_since = only, "assumed", None, None

        for book in (self.not_resident, self.unmatched):
            for tid in [t for t, when in book.items() if t not in visible and ts - when > 30]:
                del book[tid]
        for tid in [t for t in self.hits if t not in visible]:
            del self.hits[tid]
        return self.track_id

    def set_manual(self, track_id: int | None) -> None:
        if track_id is None:
            self._clear()
            self.assume_single_is_resident = False
        else:
            self.track_id, self.source, self.score, self.missing_since = track_id, "manual", None, None

    def _clear(self) -> None:
        self.track_id, self.source, self.score, self.missing_since = None, None, None, None

    def label(self, name: str = "Sandy") -> str:
        if self.track_id is None:
            return f"{name}: not identified" + (" (waiting for his face, or one person alone)"
                                               if self.assume_single_is_resident else "")
        how = {"face": f"face match {self.score:.2f}" if self.score is not None else "face",
               "assumed": "assumed: only person in view", "manual": "set by hand"}[self.source or "face"]
        return f"{name} = #{self.track_id} ({how})"


# ---------------------------------------------------------------- OpenCV parts

class FaceMatcher:
    """YuNet finds the face in each person's head crop; SFace compares it with Sandy's enrollment."""

    CROP = 192  # head crops are resized to this square before face detection

    def __init__(self, cfg: dict) -> None:
        import cv2
        import numpy as np

        self.cv2, self.np = cv2, np
        self.cfg = cfg
        self.detector = cv2.FaceDetectorYN.create(str(ensure_model("yunet")), "", (320, 320),
                                                  cfg.get("face_score", 0.6))
        self.recognizer = cv2.FaceRecognizerSF.create(str(ensure_model("sface")), "")
        self.path = ROOT / cfg.get("enrolled", "data/faces/resident.npz")
        self.default_name = cfg.get("name", "Sandy")
        self.last_faces: dict[int, tuple[tuple[int, int, int, int], float]] = {}  # tid -> (box, score) for drawing
        self.reload()

    def reload(self) -> None:
        """(Re)load the saved face. Called at start and after the face is saved again."""
        self.enrolled, self.name, self.updated = None, self.default_name, None
        if self.path.exists():
            data = self.np.load(self.path)
            self.enrolled = data["embeddings"].astype(self.np.float32)
            # The configured name (profile.resident_name) wins over the one stored with the face.
            self.name = self.cfg.get("name") or str(data["name"])
            self.updated = float(data["updated"]) if "updated" in data else None

    @property
    def ready(self) -> bool:
        return self.enrolled is not None and len(self.enrolled) > 0

    def detect(self, image):
        """All faces in an image (rows: x, y, w, h, 10 landmark coords, score), or an empty list."""
        h, w = image.shape[:2]
        self.detector.setInputSize((w, h))
        _, faces = self.detector.detect(image)
        return [] if faces is None else list(faces)

    def embed(self, image, face):
        return self.recognizer.feature(self.recognizer.alignCrop(image, face))

    def similarity(self, feature) -> float:
        return max(float(self.recognizer.match(feature, e.reshape(1, -1), 0)) for e in self.enrolled)

    def score_people(self, image, people) -> dict[int, float]:
        """track id -> similarity to Sandy, for people whose face is visible enough to judge."""
        self.last_faces = {}
        if not self.ready:
            return {}
        h, w = image.shape[:2]
        min_px = self.cfg.get("min_face_px", 20)
        out: dict[int, float] = {}
        for p in people:
            box = head_box(p, w, h)
            if box is None:
                continue
            x1, y1, x2, y2 = box
            crop = image[y1:y2, x1:x2]
            if min(crop.shape[:2]) < min_px:
                continue
            crop = self.cv2.resize(crop, (self.CROP, self.CROP))
            faces = self.detect(crop)
            if not faces:
                continue
            face = max(faces, key=lambda f: f[-1])
            score = self.similarity(self.embed(crop, face))
            out[p.track_id] = score
            k = (x2 - x1) / self.CROP
            fx, fy, fw, fh = face[:4]
            self.last_faces[p.track_id] = ((int(x1 + fx * k), int(y1 + fy * k),
                                            int(x1 + (fx + fw) * k), int(y1 + (fy + fh) * k)), score)
        return out
