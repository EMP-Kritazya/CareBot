"""Bench checker for the robot's hardware. Runs on the Pi, no network. Owned by Hardware.

    python -m apps.hwcheck pantilt     # sweep both servos, then type commands to calibrate
    python -m apps.hwcheck camera      # grab frames, report size and fps, save /tmp/hwcheck.jpg
    python -m apps.hwcheck camera --live --size 1280x720   # live view in a browser, port 8000
    python -m apps.hwcheck button      # print each press for 15 s
    python -m apps.hwcheck buzzer      # chirp, then the alarm for 3 s
    python -m apps.hwcheck eyes        # show every eye state for 3 s
    python -m apps.hwcheck --all       # every part that has a check so far

Each driver is built from the factory's registry (carebot.hw.PARTS) with its section of
config/hardware_pi.yaml, exactly as the app builds it, but with no mock fallback, so a start-up
error shows as FAIL. Parts whose checks aren't written yet print SKIP.
"""

from __future__ import annotations

import argparse
import importlib
import logging
import sys
import time

from carebot.config import load_yaml
from carebot.hw import PARTS

HW_CONFIG = "config/hardware_pi.yaml"

PANTILT_HELP = """
Calibration commands, one per line (press Enter after each):
  a PAN TILT     point to angles in degrees, e.g.  a 30 -10   (clamped to limit_deg)
  p AXIS US      raw pulse width, e.g.  p pan 1000   (AXIS is pan or tilt; 500..2500 us)
  h              home
  q              quit (PWM stops, servos go limp)

Finding the numbers for config/hardware_pi.yaml:
  center_us   'p pan 1500', then nudge (1480, 1520, ...) until the head looks straight ahead.
  us_per_deg  'p pan 1000' and 'p pan 2000', measure the angle between them in degrees,
              then us_per_deg = 1000 / that angle.
  min/max_us  widen slowly until the servo hits a stop or the head would touch something,
              then back off 50 us. Never leave a servo pressing on a stop: it draws stall current.
  invert      if 'a 30 0' turns the camera toward the LEFT of its image, set invert: true.
              Same for tilt: 'a 0 20' must tilt the camera UP.
"""


def _result(name: str, ok: bool, detail: str) -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}", flush=True)
    return ok


def _load_driver(part: str, cfg: dict):
    """Build one part's Pi driver from the factory's registry (carebot.hw.PARTS), without mocks."""
    module_name, class_name, _ = PARTS[part]
    return getattr(importlib.import_module(module_name), class_name)(cfg[part])


def check_pantilt(cfg: dict, interactive: bool = True) -> bool:
    try:
        head = _load_driver("pantilt", cfg)
    except Exception as e:  # noqa: BLE001 - report any start-up failure as a FAIL
        return _result("pantilt", False, f"driver didn't start: {e}")
    try:
        pc = cfg["pantilt"]
        lo_p, hi_p = pc["pan"]["limit_deg"]
        lo_t, hi_t = pc["tilt"]["limit_deg"]
        print("Sweeping. Watch that pan turns right for +, tilt goes up for +, and nothing hits.", flush=True)
        head.home()
        time.sleep(1.0)
        for pan, tilt in [(hi_p, 0), (0, 0), (lo_p, 0), (0, 0), (0, hi_t), (0, 0), (0, lo_t), (0, 0)]:
            head.point(pan, tilt)
            print(f"  pan {pan:+6.1f}  tilt {tilt:+6.1f}   (reported {head.position()})", flush=True)
            time.sleep(1.0)
        head.point(lo_p / 2, 0)
        moving = head.is_moving()  # must be True right after a move...
        time.sleep(pc.get("settle_ms", 250) / 1000 + 0.05)
        settled = not head.is_moving()  # ...and False once settle_ms has passed
        head.home()
        ok = _result("pantilt", moving and settled,
                     f"sweep done; is_moving after a move = {moving}, after settle_ms = {not settled}")

        if interactive:
            print(PANTILT_HELP, flush=True)
            _pantilt_console(head)
        return ok
    finally:
        head.close()


def _pantilt_console(head) -> None:
    while True:
        try:
            line = input("pantilt> ").strip().split()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        cmd, args = line[0].lower(), line[1:]
        try:
            if cmd == "q":
                return
            if cmd == "h":
                head.home()
            elif cmd == "a" and len(args) == 2:
                head.point(float(args[0]), float(args[1]))
                print(f"  now at {head.position()}")
            elif cmd == "p" and len(args) == 2 and args[0] in ("pan", "tilt"):
                head.raw_pulse(args[0], float(args[1]))
                print(f"  {args[0]} pulse {float(args[1]):.0f} us")
            else:
                print("  ? one command per line, starting with its letter, e.g.  a 30 -10   or   p pan 1500   or   h   or   q")
        except ValueError:
            print("  numbers please, e.g. a 30 -10")


CAMERA_JPG = "/tmp/hwcheck.jpg"
ROTATIONS = {90: "ROTATE_90_CLOCKWISE", 180: "ROTATE_180", 270: "ROTATE_90_COUNTERCLOCKWISE"}


def _set_fourcc(cap, cam: dict) -> None:
    """Ask a USB webcam for its compressed format (MJPG): large sizes drop to 5 fps without it."""
    import cv2

    fourcc = str(cam.get("fourcc", "MJPG"))
    if len(fourcc) == 4:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
        size = cam.get("size")
        if size:  # some drivers only apply the format with a size set after it
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(size[0]))
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(size[1]))


def _grab_usb(cam: dict, frames: int):
    """USB webcam through OpenCV, the way carebot.vision.pose opens it. Returns (label, image, got, secs)."""
    import glob

    from carebot.vision.pose import open_capture

    index = int(cam.get("index", 0))
    devices = sorted(glob.glob("/dev/video*"), key=lambda p: (len(p), p))
    hint = (f" Video devices here: {', '.join(devices) or 'none'}. Run `v4l2-ctl --list-devices`, find the"
            " webcam's first /dev/videoN, and set camera.index to N in config/hardware_pi.yaml.")
    size = cam.get("size")
    try:
        cap, _ = open_capture(index, f"{size[0]}x{size[1]}" if size else None)
    except RuntimeError as e:
        raise RuntimeError(f"{e}{hint}") from e
    _set_fourcc(cap, cam)
    try:
        for _ in range(10):  # webcams adjust exposure on the first frames
            cap.read()
        start, got, image = time.monotonic(), 0, None
        for _ in range(frames):
            ok, frame = cap.read()
            if ok:
                got, image = got + 1, frame
        elapsed = time.monotonic() - start
    finally:
        cap.release()
    if image is None:
        raise RuntimeError(f"/dev/video{index} opened but gave no frames.{hint}")
    return f"USB /dev/video{index}", image, got, elapsed


def _grab_csi(cam: dict, frames: int):
    """Ribbon-cable Pi camera through libcamera (picamera2). Returns (label, image, got, secs)."""
    try:
        from picamera2 import Picamera2
    except ImportError as e:
        raise RuntimeError(f"{e}. Install it with: sudo apt install -y python3-picamera2 "
                           "(the venv sees it because it was made with --system-site-packages)") from e
    found = Picamera2.global_camera_info()
    if not found:
        raise RuntimeError("libcamera found no camera. Check the ribbon cable (Pi 5 needs the narrow "
                           "22-pin end, contacts the right way round, latch closed), power off before "
                           "reseating, then run `rpicam-hello --list-cameras`.")
    num = int(cam.get("csi_num", 0))
    w, h = (int(v) for v in cam.get("size", (640, 480)))
    picam = Picamera2(camera_num=num)
    try:
        # "RGB888" in picamera2 is BGR byte order, the layout OpenCV and YOLO expect.
        picam.configure(picam.create_video_configuration(main={"size": (w, h), "format": "RGB888"}))
        picam.start()
        for _ in range(10):  # auto exposure settles on the first frames
            picam.capture_array()
        start, got, image = time.monotonic(), 0, None
        for _ in range(frames):
            image = picam.capture_array()
            got += 1
        elapsed = time.monotonic() - start
    finally:
        picam.close()
    model = found[num].get("Model", "?") if num < len(found) else "?"
    return f"CSI camera {num} ({model})", image, got, elapsed


def check_camera(cfg: dict, interactive: bool = True, frames: int = 60) -> bool:
    """Grab frames from the configured camera, report resolution and fps, save one (rotated) to /tmp."""
    cam = cfg.get("camera", {})
    kind, rotate = str(cam.get("type", "usb")).lower(), int(cam.get("rotate", 0))
    try:
        import cv2
    except ImportError as e:
        return _result("camera", False, f"{e}. Install OpenCV in the venv: pip install opencv-python")
    grab = {"usb": _grab_usb, "csi": _grab_csi}.get(kind)
    if grab is None:
        return _result("camera", False, f"camera.type must be usb or csi, not {kind!r}")
    try:
        label, image, got, elapsed = grab(cam, frames)
    except Exception as e:  # noqa: BLE001 - any camera failure is a FAIL with its reason
        return _result("camera", False, str(e))
    if rotate in ROTATIONS:
        image = cv2.rotate(image, getattr(cv2, ROTATIONS[rotate]))
    cv2.imwrite(CAMERA_JPG, image)
    h, w = image.shape[:2]
    fps = got / elapsed if elapsed else 0.0
    ok = got >= frames * 0.9 and fps >= 10
    return _result("camera", ok, f"{label}: {w}x{h}, {fps:.1f} fps ({got}/{frames} frames), "
                                 f"rotate {rotate}; saved {CAMERA_JPG}. On your laptop: "
                                 f"scp suyognp@carebot.local:{CAMERA_JPG} .")


LIVE_PAGE = b"""<!doctype html><html><head><meta charset="utf-8"><title>CareBot camera</title>
<style>body{margin:0;background:#111;color:#ddd;font:14px sans-serif}img{width:100%;display:block}
p{margin:8px 12px}</style></head><body><img src="/stream"><p>CareBot live view (bench test).
Stop it with Ctrl+C in the terminal.</p></body></html>"""


def live_view(cfg: dict, size: str | None = None, port: int = 8000) -> int:
    """Bench live view of the USB webcam as MJPEG at http://<pi>:<port>/ . Video stays on the robot's
    network link; nothing is recorded. Uses MJPG from the camera so large sizes still run fast."""
    import http.server
    import threading

    import cv2

    cam = cfg.get("camera", {})
    index, rotate = int(cam.get("index", 0)), int(cam.get("rotate", 0))
    cap = cv2.VideoCapture(index, cv2.CAP_V4L2)
    if not cap.isOpened():
        print(f"FAIL  camera: could not open /dev/video{index} (see `v4l2-ctl --list-devices`)")
        return 1
    if size:
        cam = {**cam, "size": [int(v) for v in size.lower().split("x")]}
    _set_fourcc(cap, cam)
    size = size or ("x".join(str(v) for v in cam["size"]) if cam.get("size") else None)
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"Camera /dev/video{index} running at {w}x{h} (asked for {size or 'its default'}).", flush=True)

    latest: dict = {"jpg": None, "n": 0}
    new_frame = threading.Condition()
    stop = threading.Event()

    def grab() -> None:
        t0, frames, fps = time.monotonic(), 0, 0.0
        while not stop.is_set():
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.05)
                continue
            if rotate in ROTATIONS:
                frame = cv2.rotate(frame, getattr(cv2, ROTATIONS[rotate]))
            frames += 1
            if time.monotonic() - t0 >= 1.0:
                fps, frames, t0 = frames / (time.monotonic() - t0), 0, time.monotonic()
            label = f"{frame.shape[1]}x{frame.shape[0]}  {fps:.0f} fps"
            cv2.putText(frame, label, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(frame, label, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)
            ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok:
                with new_frame:
                    latest["jpg"], latest["n"] = jpg.tobytes(), latest["n"] + 1
                    new_frame.notify_all()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:  # keep the terminal quiet
            pass

        def do_GET(self) -> None:
            if self.path == "/":
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(LIVE_PAGE)
                return
            if self.path != "/stream":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            seen = -1
            try:
                while not stop.is_set():
                    with new_frame:
                        new_frame.wait_for(lambda: latest["n"] != seen or stop.is_set(), timeout=2)
                        jpg, seen = latest["jpg"], latest["n"]
                    if jpg is None:
                        continue
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                     + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
            except (BrokenPipeError, ConnectionResetError):
                pass  # browser tab closed

    threading.Thread(target=grab, daemon=True).start()
    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.daemon_threads = True
    print(f"Live view on port {port}. With `ssh -L {port}:localhost:{port} ...` open http://localhost:{port} "
          f"on your laptop. Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        stop.set()
        with new_frame:
            new_frame.notify_all()
        server.server_close()
        cap.release()
    return 0


def check_eyes(cfg: dict, interactive: bool = True, seconds: float = 3.0) -> bool:
    try:
        eyes = _load_driver("eyes", cfg)
    except Exception as e:  # noqa: BLE001
        return _result("eyes", False, f"driver didn't start: {e}")
    try:
        from carebot.hw.base import EYE_STATES

        for state in [s for s in EYE_STATES if s != "off"] + ["off"]:
            print(f"  eyes: {state}", flush=True)
            eyes.set(state)
            time.sleep(seconds)
    finally:
        eyes.close()
    return _result("eyes", True, "cycled every state; check the looks against the table in docs/HARDWARE.md")


def check_buzzer(cfg: dict, interactive: bool = True) -> bool:
    try:
        buzzer = _load_driver("buzzer", cfg)
    except Exception as e:  # noqa: BLE001
        return _result("buzzer", False, f"driver didn't start: {e}")
    try:
        print("  chirp", flush=True)
        buzzer.chirp()
        time.sleep(1.0)
        print("  alarm for 3 s", flush=True)
        buzzer.start_alarm()
        time.sleep(3.0)
        buzzer.stop_alarm()
        print("  stopped", flush=True)
    finally:
        buzzer.close()
    return _result("buzzer", True, "you should have heard one chirp, then a repeating beep that stopped")


def check_button(cfg: dict, interactive: bool = True, seconds: float = 15.0) -> bool:
    try:
        button = _load_driver("button", cfg)
    except Exception as e:  # noqa: BLE001
        return _result("button", False, f"driver didn't start: {e}")
    presses = []
    button.on_press(lambda ts: (presses.append(ts), print(f"  press at {ts:.3f}", flush=True)))
    print(f"  press the button a few times in the next {seconds:.0f} s", flush=True)
    try:
        time.sleep(seconds)
    finally:
        button.close()
    return _result("button", bool(presses), f"{len(presses)} presses seen" + (
        "" if presses else "; check the wiring and button.pull_up (true = plain button to GND, "
                           "false = a module like Grove that outputs 3.3 V when pressed)"))


CHECKS = {"pantilt": check_pantilt, "camera": check_camera, "button": check_button,
          "buzzer": check_buzzer, "eyes": check_eyes}
NOT_WRITTEN_YET = ("audio",)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("part", nargs="?", choices=[*CHECKS, *NOT_WRITTEN_YET])
    ap.add_argument("--all", action="store_true", help="run every check that exists, without the console")
    ap.add_argument("--live", action="store_true", help="camera: stream a live view on --port instead")
    ap.add_argument("--size", help="camera --live: resolution to ask for, e.g. 1280x720 (default: camera.size)")
    ap.add_argument("--port", type=int, default=8000, help="camera --live: HTTP port (default 8000)")
    args = ap.parse_args()
    if not args.part and not args.all:
        ap.error("name a part (e.g. pantilt) or use --all")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    cfg = load_yaml(HW_CONFIG)

    if args.live:
        if args.part != "camera":
            ap.error("--live works with the camera part: python -m apps.hwcheck camera --live")
        return live_view(cfg, args.size, args.port)

    if args.all:
        results = [CHECKS[name](cfg, interactive=False) for name in CHECKS]
        for name in NOT_WRITTEN_YET:
            print(f"SKIP  {name}: check not written yet")
        return 0 if all(results) else 1
    if args.part in NOT_WRITTEN_YET:
        print(f"SKIP  {args.part}: check not written yet")
        return 0
    return 0 if CHECKS[args.part](cfg) else 1


if __name__ == "__main__":
    sys.exit(main())
