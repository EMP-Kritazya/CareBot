"""Live vision: camera -> pose -> face lock -> detectors -> drawn view. Shared by the apps.

`LiveVision.step()` reads one camera frame and returns a `Tick` (the drawn image, the Frame with
the resident's track id, and detector events). Face saving mode (press E) runs inside step().
Apps add their own HUD lines and keys on top: see apps/vision_live.py and apps/watcher.py.

Two ways to show it:
- a desktop window (laptop): keys from the window;
- headless (the robot, over SSH): no window, keys typed in the terminal (`press()`), and a live
  view in the browser at http://<robot>:8000. That view is **skeletons on black by default**, so
  the room is never visible on the network; `view="camera"` is for setting the robot up.
"""

from __future__ import annotations

import datetime as dt
import queue
import time
from collections import deque
from dataclasses import dataclass

from carebot.config import ROOT
from carebot.vision.detectors import Detectors, VisionEvent
from carebot.vision.identity import FaceMatcher, ResidentLock
from carebot.vision.keypoints import Frame, body_view, features
from carebot.vision.camera import open_camera, parse_source
from carebot.vision.pose import YoloPoseSource

EDGES = [(5, 6), (5, 7), (7, 9), (6, 8), (8, 10), (5, 11), (6, 12), (11, 12), (11, 13), (13, 15),
         (12, 14), (14, 16), (0, 1), (0, 2), (1, 3), (2, 4)]

# BGR colors
GREEN, BLUE, ORANGE, RED = (80, 200, 80), (230, 160, 60), (0, 150, 255), (40, 40, 230)
WHITE, GRAY, BLACK, YELLOW = (240, 240, 240), (150, 150, 150), (0, 0, 0), (0, 220, 255)

BANNERS = {
    "fall_candidate": (ORANGE, "Possible fall: watching {hold:.0f} s to see if they get back up"),
    "fall": (RED, "FALL DETECTED ({who}{oov})"),
    "fall_recovered": (GREEN, "Got back up: no alert (logged on the robot only)"),
    "got_up": (YELLOW, "Got back up after the fall: CareBot asks them to confirm they're OK"),
    "stillness": (RED, "STILLNESS ({who})"),
}


def draw_text(cv2, img, text, org, color=WHITE, scale=0.6, thick=1, bg=True):
    (w, h), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)
    x, y = org
    if bg:
        cv2.rectangle(img, (x - 4, y - h - 6), (x + w + 4, y + base + 2), BLACK, -1)
    cv2.putText(img, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def panel(cv2, img, lines, color=WHITE):
    """Dark box with instructions across the top of the image."""
    h = 34 * len(lines) + 24
    overlay = img.copy()
    cv2.rectangle(overlay, (0, 0), (img.shape[1], h), BLACK, -1)
    cv2.addWeighted(overlay, 0.75, img, 0.25, 0, img)
    for i, line in enumerate(lines):
        draw_text(cv2, img, line, (20, 40 + 34 * i), color if i else GREEN, 0.75, 2, bg=False)


def face_saving(cv2, raw, mode, enroller, matcher, key: int, typed: bool = False):
    """One frame of face saving mode, given the last key pressed.
    Returns (image to show, mode, enroller, saved_count or None). mode becomes None when done or
    skipped, "quit" if the user quits the app. typed=True: keys come from the terminal (headless)."""
    from carebot.vision.enrollment import Enroller, save_face

    name = matcher.name
    esc = "x" if typed else "Esc"
    if mode == "prompt":
        shown = cv2.flip(raw, 1)
        if matcher.ready:
            when = dt.datetime.fromtimestamp(matcher.updated).strftime("%b %d %H:%M") if matcher.updated else "earlier"
            lines = [f"Face saving: {name}'s face is saved ({len(matcher.enrolled)} samples, {when})",
                     "A = add samples (recalibrate: stand where you usually are)",
                     "N = save a new face (replaces the saved one)",
                     f"{esc} = back to watching"]
        else:
            lines = [f"No face saved for {name} yet",
                     f"E = save {name}'s face now (stand 0.5-1.5 m away, alone in view)",
                     f"S = skip for now (the only person in view is assumed to be {name})",
                     "Q = quit"]
        if typed:
            lines[0] += "   (type the letter + Enter in the terminal)"
        panel(cv2, shown, lines)
        if key == ord("q") and not matcher.ready:
            return shown, "quit", None, None
        if key in (ord("s"), 27, ord("x")) or (key == ord("q") and matcher.ready):
            return shown, None, None, None
        if (key == ord("e") and not matcher.ready) or key == ord("n"):
            return shown, "enroll", Enroller(matcher, mode="new"), None
        if key == ord("a") and matcher.ready:
            return shown, "enroll", Enroller(matcher, mode="add"), None
        return shown, "prompt", None, None

    shown, msg, ok = enroller.step(raw)
    title = f"Recalibrating {name}" if enroller.mode == "add" else f"Saving {name}'s face"
    panel(cv2, shown, [f"{title}  ({esc} = cancel)", msg], GREEN if ok else ORANGE)
    if key in (27, ord("x")):
        print("Face saving cancelled. Nothing saved.", flush=True)
        return shown, None, None, None
    if enroller.done:
        total = save_face(matcher.path, name, enroller.samples, enroller.mode)
        matcher.reload()
        return shown, None, None, total
    return shown, "enroll", enroller, None


def list_cameras(max_index: int = 6) -> None:
    """Open each camera index briefly and report it. Each camera's light may blink once."""
    import cv2

    found = False
    for i in range(max_index):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            ok, img = cap.read()
            if ok:
                found = True
                print(f"  --webcam {i}: {img.shape[1]}x{img.shape[0]}")
        cap.release()
    if not found:
        print("No cameras found. On macOS, allow camera access for VS Code / Terminal in System Settings.")
    else:
        print("Tip: the built-in Mac camera is usually 0 or 1. Unplug the USB camera and run again to tell them apart.")


@dataclass
class Tick:
    image: object                 # the drawn view (or None while face saving)
    frame: Frame | None
    events: list[VisionEvent]
    face_saving: bool = False
    quit: bool = False


class LiveVision:
    def __init__(self, cfg: dict, *, window: str, camera: str | int | None = 0, video: str | None = None,
                 size: str | None = None, rotate: int = 0, imgsz: int | None = None, device: str | None = None,
                 save_face: bool = False, headless: bool = False, view: str | None = None,
                 http_port: int | None = None, mirror: bool | None = None) -> None:
        import cv2
        import numpy as np

        self.cv2, self.np = cv2, np
        self.win = window
        self._prompted = False
        self.headless = headless
        self.view = view or ("skeleton" if headless else "camera")  # skeleton = privacy view
        self._keys: queue.Queue[int] = queue.Queue()
        self._pending_key = 255
        self.det_cfg = cfg["detectors"]
        self.detectors = Detectors(self.det_cfg)
        pose_cfg = {**self.det_cfg.get("pose", {}), **cfg.get("pose", {})}
        if imgsz:
            pose_cfg["imgsz"] = imgsz
        if device:
            pose_cfg["device"] = device
        self.pose = YoloPoseSource(pose_cfg)
        print(f"Pose model: {pose_cfg.get('model', 'yolo11n-pose.pt')} at {pose_cfg.get('imgsz', 640)} px", flush=True)
        self.video = video
        self.cam = open_camera(video if video else camera, size, rotate)
        w, h = self.cam.size()
        print(f"{self.cam.name}: {w}x{h}", flush=True)
        self.frames = self._read_frames()
        # Mirror only a laptop webcam preview (it feels natural). Never the robot's camera or a file.
        self.mirror = (parse_source(camera) != "picamera" and video is None) if mirror is None else mirror
        self.server = None
        if http_port:
            from carebot.vision.mjpeg import MjpegServer

            self.server = MjpegServer(http_port)
            print(f"Live view: http://<this machine>:{self.server.port}  ({self.view} view"
                  + (": skeletons only, the room is never shown)" if self.view == "skeleton" else ")"), flush=True)
        self.id_cfg = {**self.det_cfg.get("identity", {}),
                       "name": cfg.get("profile", {}).get("resident_name", "Sandy")}
        self.matcher = FaceMatcher(self.id_cfg)
        self.lock = self._new_lock()
        if self.matcher.ready:
            print(f"Loaded {self.matcher.name}'s saved face ({len(self.matcher.enrolled)} samples). "
                  "Press E to recalibrate.", flush=True)
        self.face_mode = "prompt" if (not self.matcher.ready or save_face) and video is None else None
        self.enroller = None
        self.every = max(1, int(self.id_cfg.get("every_n_frames", 5)))
        self.n = 0
        self.log: deque[tuple[float, VisionEvent]] = deque(maxlen=6)
        self.banner: tuple[float, tuple, str] | None = None
        self.fps, self._last = 0.0, time.monotonic()
        self.writer, self.rec_path = None, None
        self.t0: float | None = None
        self.camera_moving = lambda: (False, False)  # (any motion, tilt motion); the watcher wires the pan-tilt in
        self._last_ts = 0.0
        self.banner_suffix = ""  # e.g. the vision-only viewer adds a reminder that nothing is spoken

    def _read_frames(self):
        """(BGR image, timestamp). Live: monotonic clock. File: frame index / fps (repeatable)."""
        i = 0
        while True:
            ok, image = self.cam.read()
            if not ok:
                return
            yield image, (i / self.cam.fps if self.cam.fps else time.monotonic())
            i += 1

    def press(self, key: str | int) -> None:
        """A key typed in the terminal (headless). Thread-safe."""
        code = key if isinstance(key, int) else (27 if key in ("esc", "escape") else ord(key[0].lower()))
        self._keys.put(code)

    def _new_lock(self) -> ResidentLock:
        """A fresh face lock; it knows whether a face is saved (stricter assumptions if so)."""
        return ResidentLock.from_config(self.id_cfg, enrolled=self.matcher.ready)

    @property
    def name(self) -> str:
        return self.matcher.name

    def set_banner(self, color, text: str, ts: float | None = None) -> None:
        self.banner = (ts if ts is not None else time.monotonic(), color, text)

    def step(self) -> Tick:
        cv2 = self.cv2
        try:
            image, ts = next(self.frames)
        except StopIteration:
            return Tick(None, None, [], quit=True)
        if self.face_mode is not None:
            key, self._pending_key = self._pending_key, 255
            if self.headless and self.face_mode == "prompt" and not self._prompted:
                self._prompted = True
                print(("Face saving: type a = add samples, n = new face, x = back"
                       if self.matcher.ready else
                       f"No face saved for {self.name}: type e = save it now, s = skip, q = quit")
                      + "  (watch yourself in the live view)", flush=True)
            shown, self.face_mode, self.enroller, saved = face_saving(
                cv2, image, self.face_mode, self.enroller, self.matcher, key, typed=self.headless)
            if self.face_mode is None:
                self._prompted = False
            if saved is not None:
                self.lock = self._new_lock()
                self.set_banner(GREEN, f"{self.name}'s face saved ({saved} samples)", ts)
                print(f"{self.name}'s face saved: {saved} samples in {self.matcher.path}", flush=True)
            return Tick(shown, None, [], face_saving=True, quit=self.face_mode == "quit")

        if self.mirror:
            image = cv2.flip(image, 1)
        if self.writer is not None:
            self.writer.write(image)
        frame = self.pose.infer(image, ts)
        scores = self.matcher.score_people(image, frame.people) if self.n % self.every == 0 else {}
        self.n += 1
        self.lock.update(ts, [p.track_id for p in frame.people], scores)
        any_motion, tilt_motion = self.camera_moving()
        frame = Frame(ts=frame.ts, people=frame.people, resident_track_id=self.lock.track_id,
                      aspect=frame.aspect, camera_moving=any_motion, tilt_moving=tilt_motion)
        self.t0 = self.t0 if self.t0 is not None else ts
        self._last_ts = ts
        events = self.detectors.update(frame)
        for e in events:
            self.log.append((ts - self.t0, e))
            color, tmpl = BANNERS[e.kind]
            text = tmpl.format(hold=self.det_cfg["fall"]["hold_s"],
                               who=self.name if e.who == "resident" else "a visitor",
                               oov=", fell out of view" if e.out_of_view else "")
            if e.kind in ("fall", "stillness", "got_up"):
                text += self.banner_suffix
            self.set_banner(color, text, ts)
        canvas = image if self.view == "camera" else self.np.zeros_like(image)  # skeleton view: no room
        self._draw(canvas, frame)
        return Tick(canvas, frame, events)

    def restart_stillness(self, cooldown: bool, backoff: bool) -> None:
        """An incident closed: restart the stillness timer on the camera's clock."""
        self.detectors.stillness.restart(self._last_ts, cooldown=cooldown, backoff=backoff)

    def resident_posture(self, frame: Frame | None) -> str:
        return self.detectors.resident_posture(frame) if frame else "out_of_view"

    # ---- drawing

    def _draw(self, image, frame: Frame) -> None:
        cv2, c = self.cv2, self.det_cfg
        h, w = image.shape[:2]
        for p in frame.people:
            feat = features(p, frame.aspect, c["min_kp_conf"], c["lying_deg"], c["sitting_knee_ratio"])
            tr = self.detectors.fall.tracks.get(p.track_id)
            state = tr.state if tr else "watch"
            is_res = p.track_id == self.lock.track_id
            color = {"candidate": ORANGE, "confirmed": RED}.get(state, GREEN if is_res else BLUE)
            pts = [(int(x * w), int(y * h), conf) for x, y, conf in p.keypoints]
            for a, b in EDGES:
                if pts[a][2] >= c["min_kp_conf"] and pts[b][2] >= c["min_kp_conf"]:
                    cv2.line(image, pts[a][:2], pts[b][:2], color, 3, cv2.LINE_AA)
            for x, y, conf in pts:
                if conf >= c["min_kp_conf"]:
                    cv2.circle(image, (x, y), 4, WHITE, -1, cv2.LINE_AA)
            desc = f"#{p.track_id} {self.name.upper() if is_res else 'visitor'}"
            if feat:
                desc += f"  {feat.posture} {feat.torso_deg:.0f}deg"
            else:
                view = body_view(p, frame.aspect, c["min_kp_conf"])
                desc += f"  {view[0]} (hips hidden)" if view else "  (body not visible enough)"
            if state != "watch":
                desc += f"  [{state}]"
            visible = [pt for pt in pts if pt[2] >= c["min_kp_conf"]]
            if visible:
                top = min(visible, key=lambda pt: pt[1])
                draw_text(cv2, image, desc, (max(5, top[0] - 60), max(25, top[1] - 20)), color)
        for tid, ((x1, y1, x2, y2), sc) in self.matcher.last_faces.items():
            ok = sc >= self.lock.match_threshold
            cv2.rectangle(image, (x1, y1), (x2, y2), GREEN if ok else BLUE, 2)
            draw_text(cv2, image, f"{self.name if ok else 'not ' + self.name} {sc:.2f}", (x1, y2 + 18),
                      GREEN if ok else BLUE, 0.5)

    def hud(self, image, frame: Frame, extra: list[str] | None = None) -> None:
        """Status lines top-left, event log bottom-left, banner across the middle."""
        cv2 = self.cv2
        h, w = image.shape[:2]
        now = time.monotonic()
        inst = 1 / max(1e-3, now - self._last)
        self.fps = 0.9 * self.fps + 0.1 * inst if self.fps else inst
        self._last = now
        st, sc = self.detectors.stillness, self.det_cfg["stillness"]
        still = f"stillness: {st.reason}"
        if st.last_motion is not None:
            still += f"   movement {st.last_motion:.3f} (still below {sc['motion_threshold']})"
        if st.profile == "demo":
            still = "DEMO TIMERS  " + still
        people = f"{self.fps:4.1f} fps   people: {len(frame.people)}"
        if self.pose.dropped:
            people += f"   (ignored {self.pose.dropped} non-person detection{'s' if self.pose.dropped > 1 else ''})"
        who = self.lock.label(self.name)
        who += (f"   [saved face: {len(self.matcher.enrolled)} samples, E to recalibrate]" if self.matcher.ready
                else "   [no face saved: press E]")
        lines = [people, who, still] + (extra or [])
        if self.writer is not None:
            lines.append(f"REC  {self.rec_path.name}")
        for i, line in enumerate(lines):
            draw_text(cv2, image, line, (12, 28 + 28 * i), RED if line.startswith("REC") else WHITE)
        for i, (t, e) in enumerate(reversed(self.log)):
            draw_text(cv2, image, f"{t:6.1f}s  {e.kind}  #{e.track_id} ({e.who})", (12, h - 20 - 26 * i),
                      BANNERS[e.kind][0], 0.55)
        if self.banner and now - self.banner[0] < 4 or (self.banner and frame.ts - self.banner[0] < 4):
            _, color, text = self.banner
            cv2.rectangle(image, (0, h // 2 - 40), (w, h // 2 + 30), color, -1)
            draw_text(cv2, image, text, (20, h // 2), WHITE, 0.8, 2, bg=False)

    def show(self, image) -> int:
        """Show a frame (window and/or browser view). Returns the key pressed (255 = none)."""
        if image is not None and self.server is not None:
            self.server.publish(image)
        if self.headless:
            try:
                key = self._keys.get_nowait()
            except queue.Empty:
                key = 255
        else:
            if image is not None:
                self.cv2.imshow(self.win, image)
            key = self.cv2.waitKey(1) & 0xFF
            if key == 255:  # nothing in the window: maybe a key was typed in the terminal
                try:
                    key = self._keys.get_nowait()
                except queue.Empty:
                    pass
        if self.face_mode is not None:
            self._pending_key = key  # face saving reads it on the next step
        return key

    # ---- keys shared by every app

    def key(self, key: int, frame: Frame | None) -> bool:
        """Handle vision keys. Returns True if the key was used."""
        if key == ord("e") and self.video is None:
            self.face_mode = "prompt"
        elif key == ord("n") and frame and frame.people:
            ids = sorted(p.track_id for p in frame.people)
            nxt = next((i for i in ids if self.lock.track_id is None or i > self.lock.track_id), ids[0])
            self.lock.set_manual(nxt)
        elif key == ord("u"):
            self.lock.set_manual(None)
        elif key == ord("a"):
            self.lock = self._new_lock()
        elif key == ord("m"):
            self.mirror = not self.mirror
        elif key == ord("r"):
            self._toggle_recording()
        else:
            return False
        return True

    def _toggle_recording(self) -> None:
        cv2 = self.cv2
        if self.writer is None:
            rec_dir = ROOT / "recordings"
            rec_dir.mkdir(exist_ok=True)
            self.rec_path = rec_dir / f"{dt.datetime.now():%Y%m%d-%H%M%S}.mp4"
            w, h = self.cam.size()
            self.writer = cv2.VideoWriter(str(self.rec_path), cv2.VideoWriter_fourcc(*"mp4v"), 15, (w, h))
            print(f"recording to {self.rec_path}", flush=True)
        else:
            self.writer.release()
            print(f"saved {self.rec_path}", flush=True)
            self.writer = None

    def close(self) -> None:
        if self.writer is not None:
            self.writer.release()
        self.cam.release()
        if self.server is not None:
            self.server.close()
        if not self.headless:
            self.cv2.destroyAllWindows()
