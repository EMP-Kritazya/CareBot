"""Live vision check: webcam or video in, skeletons + detector events on screen.

    python -m apps.vision_live                  # webcam 0
    python -m apps.vision_live --webcam 1       # another camera
    python -m apps.vision_live --video clip.mp4
    python -m apps.vision_live --stillness 10   # shorter stillness timer for testing
    python -m apps.vision_live --list-cameras   # which --webcam number is which camera

Face saving: on start, if no face is saved yet, the app asks you to save Ruth's face (or skip).
After that the saved face is loaded on every run. Press E any time to recalibrate (add samples,
e.g. at the robot's real distance) or save a new face (replace). Until a face is saved, the only
person in view is assumed to be Ruth.

Keys (click the window first):
    q / Esc   quit
    e         face saving: recalibrate or save a new face
    n         override: make the next visible person "Ruth"
    u         override: nobody is Ruth
    a         back to automatic (face lock)
    m         mirror the view on/off
    r         start/stop recording the raw camera to recordings/ (local only, git-ignored)

Vision only: no voice, no alerts. It shows what CareBot would do. Every event is also printed in the
terminal so you can copy it into feedback.
"""

from __future__ import annotations

import argparse
import datetime as dt
import time
from collections import deque

from carebot.config import ROOT, load_config
from carebot.vision.detectors import Detectors, VisionEvent
from carebot.vision.identity import FaceMatcher, ResidentLock
from carebot.vision.keypoints import Frame, body_view, features
from carebot.vision.pose import YoloPoseSource, open_capture, read_frames

EDGES = [(5, 6), (5, 7), (7, 9), (6, 8), (8, 10), (5, 11), (6, 12), (11, 12), (11, 13), (13, 15),
         (12, 14), (14, 16), (0, 1), (0, 2), (1, 3), (2, 4)]

# BGR colors
GREEN, BLUE, ORANGE, RED, WHITE, GRAY, BLACK = (80, 200, 80), (230, 160, 60), (0, 150, 255), (40, 40, 230), (240, 240, 240), (150, 150, 150), (0, 0, 0)

BANNERS = {
    "fall_candidate": (ORANGE, "Possible fall: watching {hold:.0f} s to see if they get back up"),
    "fall": (RED, "FALL DETECTED ({who}{oov}): CareBot would start a check-in now"),
    "fall_recovered": (GREEN, "Got back up: no alert (logged on the robot only)"),
    "stillness": (RED, "STILLNESS ({who}): CareBot would ask \"Are you OK?\""),
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


def face_saving(cv2, win, raw, mode, enroller, matcher):
    """One frame of face saving mode. Returns (mode, enroller, saved_count or None).
    mode becomes None when done or skipped, "quit" if the user quits the app."""
    from carebot.vision.enrollment import Enroller, save_face

    name = matcher.name
    if mode == "prompt":
        shown = cv2.flip(raw, 1)
        if matcher.ready:
            when = dt.datetime.fromtimestamp(matcher.updated).strftime("%b %d %H:%M") if matcher.updated else "earlier"
            lines = [f"Face saving: {name}'s face is saved ({len(matcher.enrolled)} samples, {when})",
                     "A = add samples (recalibrate: stand where you usually are)",
                     "N = save a new face (replaces the saved one)",
                     "Esc = back to watching"]
        else:
            lines = [f"No face saved for {name} yet",
                     f"E = save {name}'s face now (stand 0.5-1.5 m away, alone in view)",
                     f"S = skip for now (the only person in view is assumed to be {name})",
                     "Q = quit"]
        panel(cv2, shown, lines)
        cv2.imshow(win, shown)
        key = cv2.waitKey(1) & 0xFF
        if key == ord("q") and not matcher.ready:
            return "quit", None, None
        if key in (ord("s"), 27) or (key == ord("q") and matcher.ready):
            return None, None, None
        if (key == ord("e") and not matcher.ready) or key == ord("n"):
            return "enroll", Enroller(matcher, mode="new"), None
        if key == ord("a") and matcher.ready:
            return "enroll", Enroller(matcher, mode="add"), None
        return "prompt", None, None

    shown, msg, ok = enroller.step(raw)
    title = f"Recalibrating {name}" if enroller.mode == "add" else f"Saving {name}'s face"
    panel(cv2, shown, [f"{title}  (Esc = cancel)", msg], GREEN if ok else ORANGE)
    cv2.imshow(win, shown)
    if (cv2.waitKey(1) & 0xFF) == 27:
        print("Face saving cancelled. Nothing saved.", flush=True)
        return None, None, None
    if enroller.done:
        total = save_face(matcher.path, name, enroller.samples, enroller.mode)
        matcher.reload()
        return None, None, total
    return "enroll", enroller, None


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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--webcam", type=int, default=0)
    ap.add_argument("--video")
    ap.add_argument("--config", default="config/mock.yaml")
    ap.add_argument("--stillness", type=float, help="override stillness seconds for testing")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--device", help="cpu, mps, cuda (default: auto)")
    ap.add_argument("--size", default="native",
                    help="camera resolution, e.g. 1280x720, or 'native' (default: the camera's own, no crop)")
    ap.add_argument("--list-cameras", action="store_true", help="show which --webcam number is which camera, then exit")
    ap.add_argument("--save-face", action="store_true", help="open face saving before watching, even if a face is saved")
    args = ap.parse_args()

    if args.list_cameras:
        list_cameras()
        return

    import cv2

    cfg = load_config(args.config)
    det_cfg = cfg["detectors"]
    if args.stillness:
        det_cfg["stillness"]["seconds"] = args.stillness
    detectors = Detectors(det_cfg)
    print("Loading YOLO11n-pose (first run downloads ~6 MB)...", flush=True)
    pose = YoloPoseSource({**det_cfg.get("pose", {}), "imgsz": args.imgsz, "device": args.device})
    source = args.video or args.webcam
    cap, file_fps = open_capture(source, None if args.size == "native" else args.size)
    if args.video is None:
        print(f"camera {args.webcam}: {int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x"
              f"{int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))}", flush=True)
    mirror = args.video is None
    id_cfg = {**det_cfg.get("identity", {}), "name": cfg.get("profile", {}).get("resident_name", "Ruth")}
    lock = ResidentLock.from_config(id_cfg)
    matcher = FaceMatcher(id_cfg)
    if matcher.ready:
        print(f"Loaded {matcher.name}'s saved face ({len(matcher.enrolled)} samples). Press E to recalibrate.", flush=True)
    # Face saving mode: "prompt" (choose), "enroll" (collecting), or None (watching).
    face_mode = "prompt" if (not matcher.ready or args.save_face) and args.video is None else None
    enroller = None
    every = max(1, int(id_cfg.get("every_n_frames", 5)))
    n_frame = 0
    log: deque[tuple[float, VisionEvent]] = deque(maxlen=7)
    banner: tuple[float, tuple, str] | None = None
    fps, last = 0.0, time.monotonic()
    writer, rec_path = None, None
    t0 = None
    win = "CareBot vision (q to quit)"

    for image, ts in read_frames(cap, file_fps):
        if face_mode is not None:
            face_mode, enroller, saved = face_saving(cv2, win, image, face_mode, enroller, matcher)
            if saved is not None:
                lock = ResidentLock.from_config(id_cfg)
                banner = (ts, GREEN, f"{matcher.name}'s face saved ({saved} samples)")
                print(f"{matcher.name}'s face saved: {saved} samples in {matcher.path}", flush=True)
            if face_mode == "quit":
                break
            continue
        if mirror:
            image = cv2.flip(image, 1)
        raw = image.copy() if writer is not None else None
        frame = pose.infer(image, ts)
        scores = matcher.score_people(image, frame.people) if n_frame % every == 0 else {}
        n_frame += 1
        lock.update(ts, [p.track_id for p in frame.people], scores)
        frame = Frame(ts=frame.ts, people=frame.people, resident_track_id=lock.track_id, aspect=frame.aspect)
        t0 = t0 if t0 is not None else ts
        events = detectors.update(frame)

        for e in events:
            log.append((ts - t0, e))
            color, tmpl = BANNERS[e.kind]
            text = tmpl.format(hold=det_cfg["fall"]["hold_s"], who=matcher.name if e.who == "resident" else "a visitor",
                               oov=", fell out of view" if e.out_of_view else "")
            banner = (ts, color, text)
            print(f"[{ts - t0:7.1f}s] {e.kind:<15} track #{e.track_id} ({e.who})"
                  + (" out of view" if e.out_of_view else ""), flush=True)

        # ---- draw people
        h, w = image.shape[:2]
        for p in frame.people:
            feat = features(p, frame.aspect, det_cfg["min_kp_conf"], det_cfg["lying_deg"], det_cfg["sitting_knee_ratio"])
            tr = detectors.fall.tracks.get(p.track_id)
            state = tr.state if tr else "watch"
            is_res = p.track_id == lock.track_id
            color = {"candidate": ORANGE, "confirmed": RED}.get(state, GREEN if is_res else BLUE)
            pts = [(int(x * w), int(y * h), c) for x, y, c in p.keypoints]
            for a, b in EDGES:
                if pts[a][2] >= det_cfg["min_kp_conf"] and pts[b][2] >= det_cfg["min_kp_conf"]:
                    cv2.line(image, pts[a][:2], pts[b][:2], color, 3, cv2.LINE_AA)
            for x, y, c in pts:
                if c >= det_cfg["min_kp_conf"]:
                    cv2.circle(image, (x, y), 4, WHITE, -1, cv2.LINE_AA)
            name = matcher.name.upper() if is_res else "visitor"
            desc = f"#{p.track_id} {name}"
            if feat:
                desc += f"  {feat.posture} {feat.torso_deg:.0f}deg"
            else:
                view = body_view(p, frame.aspect, det_cfg["min_kp_conf"])
                desc += f"  {view[0]} (hips hidden)" if view else "  (body not visible enough)"
            if state != "watch":
                desc += f"  [{state}]"
            visible = [pt for pt in pts if pt[2] >= det_cfg["min_kp_conf"]]
            if visible:
                top = min(visible, key=lambda pt: pt[1])
                draw_text(cv2, image, desc, (max(5, top[0] - 60), max(25, top[1] - 20)), color)

        # ---- face boxes (only on frames where faces were checked)
        for tid, ((x1, y1, x2, y2), sc) in matcher.last_faces.items():
            ok = sc >= lock.match_threshold
            cv2.rectangle(image, (x1, y1), (x2, y2), GREEN if ok else BLUE, 2)
            draw_text(cv2, image, f"{matcher.name if ok else 'not ' + matcher.name} {sc:.2f}", (x1, y2 + 18),
                      GREEN if ok else BLUE, 0.5)

        # ---- HUD
        now = time.monotonic()
        fps = 0.9 * fps + 0.1 * (1 / max(1e-3, now - last)) if fps else 1 / max(1e-3, now - last)
        last = now
        st = detectors.stillness
        sc = det_cfg["stillness"]
        still = f"stillness: {st.reason}"
        if st.still_since is not None:
            still += f"  {ts - st.still_since:4.1f} / {sc['seconds']:.0f} s"
        if st.last_motion is not None:
            still += f"   movement {st.last_motion:.3f} (still below {sc['motion_threshold']})"
        people = f"{fps:4.1f} fps   people: {len(frame.people)}"
        if pose.dropped:
            people += f"   (ignored {pose.dropped} non-person detection{'s' if pose.dropped > 1 else ''})"
        who = lock.label(matcher.name)
        who += f"   [saved face: {len(matcher.enrolled)} samples, E to recalibrate]" if matcher.ready \
            else "   [no face saved: press E]"
        hud = [people, who, still]
        if writer is not None:
            hud.append(f"REC  {rec_path.name}")
        for i, line in enumerate(hud):
            draw_text(cv2, image, line, (12, 28 + 28 * i), RED if line.startswith("REC") else WHITE)

        for i, (t, e) in enumerate(reversed(log)):
            color = BANNERS[e.kind][0]
            draw_text(cv2, image, f"{t:6.1f}s  {e.kind}  #{e.track_id} ({e.who})", (12, h - 20 - 26 * i), color, 0.55)

        if banner and ts - banner[0] < 4:
            _, color, text = banner
            cv2.rectangle(image, (0, h // 2 - 40), (w, h // 2 + 30), color, -1)
            draw_text(cv2, image, text, (20, h // 2), WHITE, 0.8, 2, bg=False)

        cv2.imshow(win, image)
        if raw is not None:
            writer.write(raw)

        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), 27):
            break
        if key == ord("e") and args.video is None:
            face_mode = "prompt"
            continue
        if key == ord("n") and frame.people:
            ids = sorted(p.track_id for p in frame.people)
            nxt = next((i for i in ids if lock.track_id is None or i > lock.track_id), ids[0])
            lock.set_manual(nxt)
        elif key == ord("u"):
            lock.set_manual(None)
        elif key == ord("a"):
            lock = ResidentLock.from_config(id_cfg)
        elif key == ord("m"):
            mirror = not mirror
        elif key == ord("r"):
            if writer is None:
                rec_dir = ROOT / "recordings"
                rec_dir.mkdir(exist_ok=True)
                rec_path = rec_dir / f"{dt.datetime.now():%Y%m%d-%H%M%S}.mp4"
                writer = cv2.VideoWriter(str(rec_path), cv2.VideoWriter_fourcc(*"mp4v"), 15,
                                         (image.shape[1], image.shape[0]))
                print(f"recording to {rec_path}", flush=True)
            else:
                writer.release()
                print(f"saved {rec_path}", flush=True)
                writer = None

    if writer is not None:
        writer.release()
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
