"""Live vision check: webcam or video in, skeletons + detector events on screen. No voice, no alerts.

    python -m apps.vision_live                  # webcam 0
    python -m apps.vision_live --webcam 1       # another camera
    python -m apps.vision_live --video clip.mp4
    python -m apps.vision_live --demo           # short demo timers
    python -m apps.vision_live --stillness 10   # every stillness timer = 10 s (quick test)
    python -m apps.vision_live --list-cameras   # which --webcam number is which camera

Face saving: on start, if no face is saved yet, the app asks you to save the resident's face (or
skip). After that the saved face is loaded on every run. Press E any time to recalibrate (add
samples, e.g. at the robot's real distance) or save a new face (replace). Until a face is saved,
the only person in view is assumed to be the resident.

Keys (click the window first):
    q / Esc   quit
    e         face saving: recalibrate or save a new face
    n         override: make the next visible person the resident
    u         override: nobody is the resident
    a         back to automatic (face lock)
    m         mirror the view on/off
    r         start/stop recording the raw camera to recordings/ (local only, git-ignored)

For the full robot behavior (voice, check-ins, alerts) run `python -m apps.watcher`.
"""

from __future__ import annotations

import argparse
import sys
import threading

from carebot.config import apply_stillness_flags, load_config
from carebot.vision.live import LiveVision, list_cameras


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--camera", help="usb (the robot's Logitech webcam), a webcam index or /dev/videoN, or a video file")
    ap.add_argument("--webcam", type=int, help="same as --camera N")
    ap.add_argument("--video")
    ap.add_argument("--config", default="config/mock.yaml")
    ap.add_argument("--demo", action="store_true",
                    help="short demo timers (sitting 10 s, standing 7 s, slumped 5 s, cooldown 5 s)")
    ap.add_argument("--stillness", type=float, metavar="SECONDS",
                    help="set every stillness timer to this many seconds (quick testing)")
    ap.add_argument("--imgsz", type=int, help="pose input size (default: pose.imgsz in the config)")
    ap.add_argument("--device", help="cpu, mps, cuda (default: auto)")
    ap.add_argument("--size", help="camera resolution, e.g. 1280x720 (default: the camera's own / config)")
    ap.add_argument("--headless", action="store_true", help="no window: type keys in the terminal, view in the browser")
    ap.add_argument("--view", choices=("skeleton", "camera"), help="browser view (default skeleton when headless)")
    ap.add_argument("--http-port", type=int, help="serve the live view on this port (default 8000 when headless)")
    ap.add_argument("--list-cameras", action="store_true", help="show which --webcam number is which camera, then exit")
    ap.add_argument("--save-face", action="store_true", help="open face saving before watching, even if a face is saved")
    args = ap.parse_args()

    if args.list_cameras:
        list_cameras()
        return

    cfg = load_config(args.config)
    apply_stillness_flags(cfg, demo=args.demo, override_s=args.stillness)
    cam_cfg = {**cfg.get("hw", {}).get("camera", {}), **cfg.get("camera", {})}
    camera = args.camera if args.camera is not None else (args.webcam if args.webcam is not None
                                                          else cam_cfg.get("source", "picamera" if cam_cfg.get("type") == "csi"
                                                                   else cam_cfg.get("index", 0)))
    vision = LiveVision(cfg, window="CareBot vision (q to quit)", camera=camera, video=args.video,
                        size=args.size or cam_cfg.get("size"), rotate=int(cam_cfg.get("rotate", 0)),
                        mirror=cam_cfg.get("mirror"),
                        imgsz=args.imgsz, device=args.device, save_face=args.save_face, headless=args.headless,
                        view=args.view, http_port=args.http_port or (8000 if args.headless else None))
    vision.banner_suffix = "  (vision only: no voice here)"
    print("\nVISION ONLY: this never speaks or alerts. For the full robot (voice, check-ins, alerts) run:"
          "  python -m apps.watcher\n", flush=True)
    quit_flag = threading.Event()

    def console():  # keys typed in the terminal (the only way when headless)
        for line in sys.stdin:
            low = line.strip().lower()
            if low == "q" and vision.face_mode is None:
                quit_flag.set()
                return
            if low:
                vision.press(low if len(low) == 1 or low in ("esc", "escape") else low[0])

    threading.Thread(target=console, daemon=True).start()
    t0 = None
    while not quit_flag.is_set():
        tick = vision.step()
        if tick.quit:
            break
        if tick.face_saving:
            vision.show(tick.image)
            continue
        t0 = t0 if t0 is not None else tick.frame.ts
        for e in tick.events:
            print(f"[{tick.frame.ts - t0:7.1f}s] {e.kind:<15} track #{e.track_id} ({e.who})"
                  + (" out of view" if e.out_of_view else ""), flush=True)
        vision.hud(tick.image, tick.frame, ["VISION ONLY (no voice). Full robot: python -m apps.watcher"])
        key = vision.show(tick.image)
        if key in (ord("q"), 27):
            break
        vision.key(key, tick.frame)
    vision.close()


if __name__ == "__main__":
    main()
