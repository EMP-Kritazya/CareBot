"""Pose speed check: which model and input size to use on this machine (run it on the Pi).

    python -m apps.bench_pose                    # benchmark every model found, on sample photos
    python -m apps.bench_pose --camera usb       # ...on live frames from the robot's webcam
    python -m apps.bench_pose --export           # (re)export NCNN models at 640 / 416 / 320 into models/

For each model it prints the median time per frame, the frames per second, and how closely its
keypoints agree with the original PyTorch model (so a faster model isn't quietly worse).
CareBot needs roughly 8+ fps for falls; pick the largest size that gets there, then set
`pose.model` and `pose.imgsz` in config/pi.yaml.
"""

from __future__ import annotations

import argparse
import shutil
import statistics
import time

from carebot.config import ROOT

SIZES = (640, 416, 320)


def export() -> None:
    from ultralytics import YOLO

    for size in SIZES:
        path = YOLO("yolo11n-pose.pt").export(format="ncnn", imgsz=size, verbose=False)
        dest = ROOT / "models" / f"yolo11n-pose_{size}_ncnn_model"
        shutil.rmtree(dest, ignore_errors=True)
        shutil.move(path, dest)
        print(f"exported {size} px -> {dest.relative_to(ROOT)}", flush=True)


def frames(camera: str | None, n: int):
    import cv2

    if camera:
        from carebot.vision.camera import open_camera

        cam = open_camera(camera)
        out = []
        for _ in range(n):
            ok, img = cam.read()
            if ok:
                out.append(img)
        cam.release()
        return out
    from ultralytics.utils import ASSETS

    imgs = [cv2.imread(str(ASSETS / name)) for name in ("zidane.jpg", "bus.jpg")]
    return [imgs[i % 2] for i in range(n)]


def keypoints(result):
    if result.keypoints is None or result.keypoints.xyn is None:
        return []
    return result.keypoints.xyn.tolist()


def agreement(ref: list, got: list) -> float | None:
    """Mean distance (in % of the frame) between each reference person's keypoints and the closest
    detected person's. None if nobody to compare."""
    if not ref or not got:
        return None
    dists = []
    for person in ref:
        best = min(got, key=lambda g: sum(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b in zip(person, g)))
        pts = [(abs(a[0] - b[0]) + abs(a[1] - b[1])) / 2 for a, b in zip(person, best) if a != [0, 0]]
        if pts:
            dists.append(sum(pts) / len(pts))
    return 100 * sum(dists) / len(dists) if dists else None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--export", action="store_true", help="export NCNN models first")
    ap.add_argument("--camera", help="benchmark on live frames: usb (the robot's webcam), a webcam index, or picamera")
    ap.add_argument("--frames", type=int, default=30)
    args = ap.parse_args()
    if args.export:
        export()

    from ultralytics import YOLO

    imgs = frames(args.camera, args.frames)
    if not imgs:
        raise SystemExit("no frames to test on")
    candidates = [("yolo11n-pose.pt", 640)] + [(f"models/yolo11n-pose_{s}_ncnn_model", s) for s in SIZES
                                               if (ROOT / "models" / f"yolo11n-pose_{s}_ncnn_model").exists()]
    reference = None
    print(f"{len(imgs)} frames, {imgs[0].shape[1]}x{imgs[0].shape[0]}\n")
    print(f"{'model':38} {'size':>5} {'ms/frame':>9} {'fps':>6} {'people':>7} {'vs original':>12}")
    for name, size in candidates:
        path = str(ROOT / name) if (ROOT / name).exists() else name
        model = YOLO(path, task="pose")
        model.predict(imgs[0], imgsz=size, verbose=False)  # warm up
        times, people, kps = [], [], []
        for img in imgs:
            t = time.perf_counter()
            r = model.predict(img, imgsz=size, conf=0.5, verbose=False)[0]
            times.append((time.perf_counter() - t) * 1000)
            people.append(len(r.boxes) if r.boxes is not None else 0)
            kps.append(keypoints(r))
        if reference is None:
            reference = kps
        diffs = [d for d in (agreement(a, b) for a, b in zip(reference, kps)) if d is not None]
        ms = statistics.median(times)
        diff = f"{statistics.mean(diffs):.1f}% off" if diffs and name != candidates[0][0] else "reference"
        print(f"{name:38} {size:>5} {ms:>9.1f} {1000 / ms:>6.1f} {statistics.mean(people):>7.1f} {diff:>12}")
    print("\nPick the largest size at 8+ fps on the robot; set pose.model / pose.imgsz in config/pi.yaml.")


if __name__ == "__main__":
    main()
