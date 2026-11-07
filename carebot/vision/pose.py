"""Pose sources: camera or video file in, (image, Frame) out.

YoloPoseSource runs YOLO11n-pose with ByteTrack, so every person keeps a track id across frames.
Keypoints come out normalized (0..1) in COCO-17 order, the same format the detectors use.
Detections that aren't really a person (a raised hand, a reflection) are filtered out by
`filter_people`, with thresholds from the `pose` section of config/detectors.yaml.
Heavy imports (ultralytics, cv2) happen inside the class, so the rest of CareBot doesn't need them.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterator

from carebot.vision.keypoints import Frame, Keypoint, Person

Box = tuple[float, float, float, float]  # x1, y1, x2, y2, normalized


@dataclass(frozen=True)
class Detection:
    track_id: int
    box: Box
    keypoints: tuple[Keypoint, ...]


def _area(b: Box) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _inside_share(inner: Box, outer: Box) -> float:
    """Share of `inner`'s area that lies inside `outer`."""
    ix = max(0.0, min(inner[2], outer[2]) - max(inner[0], outer[0]))
    iy = max(0.0, min(inner[3], outer[3]) - max(inner[1], outer[1]))
    a = _area(inner)
    return (ix * iy) / a if a > 0 else 0.0


def filter_people(dets: list[Detection], cfg: dict) -> list[Detection]:
    """Keep detections that look like a whole person. Pure, so it's unit tested.

    Drops: too few confident keypoints, tiny boxes, and small boxes sitting mostly inside a bigger
    person's box (YOLO sometimes calls a raised hand a second person).
    """
    min_kps = cfg.get("min_keypoints", 6)
    kp_conf = cfg.get("min_keypoint_conf", 0.5)
    min_box = cfg.get("min_box_frac", 0.12)
    nested_area = cfg.get("nested_area_frac", 0.35)
    nested_overlap = cfg.get("nested_overlap", 0.8)

    def solid(d: Detection) -> bool:
        w, h = d.box[2] - d.box[0], d.box[3] - d.box[1]
        good = sum(1 for k in d.keypoints if k[2] >= kp_conf)
        return good >= min_kps and max(w, h) >= min_box

    kept = [d for d in dets if solid(d)]
    out = []
    for d in kept:
        nested = any(o is not d and _area(d.box) < nested_area * _area(o.box)
                     and _inside_share(d.box, o.box) >= nested_overlap for o in kept)
        if not nested:
            out.append(d)
    return out


class YoloPoseSource:
    def __init__(self, cfg: dict | None = None) -> None:
        from ultralytics import YOLO  # lazy: big import

        cfg = cfg or {}
        self.cfg = cfg
        self.model = YOLO(cfg.get("model", "yolo11n-pose.pt"))  # downloads once on first use
        self.imgsz = cfg.get("imgsz", 640)
        self.conf = cfg.get("conf", 0.5)
        self.tracker = cfg.get("tracker", "bytetrack.yaml")
        self.device = cfg.get("device")  # None = let ultralytics pick
        self.dropped = 0  # detections filtered out in the last frame (for the live view)

    def infer(self, image, ts: float) -> Frame:
        h, w = image.shape[:2]
        r = self.model.track(image, persist=True, tracker=self.tracker, imgsz=self.imgsz,
                             conf=self.conf, device=self.device, verbose=False)[0]
        dets: list[Detection] = []
        if r.boxes is not None and r.boxes.id is not None and r.keypoints is not None:
            ids = r.boxes.id.int().tolist()
            boxes = r.boxes.xyxyn.tolist()
            xyn = r.keypoints.xyn.tolist()
            confs = r.keypoints.conf.tolist() if r.keypoints.conf is not None else [[1.0] * 17] * len(ids)
            for tid, box, pts, cs in zip(ids, boxes, xyn, confs):
                kps = tuple((float(x), float(y), float(c)) for (x, y), c in zip(pts, cs))
                dets.append(Detection(tid, tuple(box), kps))
        kept = filter_people(dets, self.cfg)
        self.dropped = len(dets) - len(kept)
        people = tuple(Person(track_id=d.track_id, keypoints=d.keypoints) for d in kept)
        return Frame(ts=ts, people=people, aspect=w / h)


def open_capture(source: int | str, size: str | None = None):
    """Webcam index or video path -> (cv2.VideoCapture, fps or None for live).
    size: "WIDTHxHEIGHT" to request a resolution, or None for the camera's native one. Forcing a
    16:9 size on a 4:3 camera crops the picture (looks zoomed in), so native is the default."""
    import cv2

    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        hint = (" On macOS, allow camera access for your terminal / VS Code in System Settings > "
                "Privacy & Security > Camera, then restart it.") if isinstance(source, int) else ""
        raise RuntimeError(f"Could not open {source!r}.{hint}")
    if isinstance(source, int):
        if size:
            w, h = (int(v) for v in size.lower().split("x"))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        return cap, None
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    return cap, fps


def read_frames(cap, file_fps: float | None) -> Iterator[tuple[object, float]]:
    """Yields (bgr image, timestamp). Live: monotonic clock. File: frame index / fps (repeatable)."""
    i = 0
    while True:
        ok, image = cap.read()
        if not ok:
            return
        yield image, (i / file_fps if file_fps else time.monotonic())
        i += 1
