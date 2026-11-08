"""Save the resident's face (local only). The live app does the same when you press E.

    python -m apps.enroll --webcam 1            # new face (replaces the saved one)
    python -m apps.enroll --webcam 1 --add      # recalibrate: add samples to the saved face

New face: stand 0.5-1.5 m from the camera, alone in view, look at it and slowly turn your head a
little each way. Recalibrate: stand where you usually are (e.g. across the room from the robot).

Saved to data/faces/resident.npz on this machine only (git-ignored). Only save the face of someone
who has agreed to it. Delete the file to forget them.
"""

from __future__ import annotations

import argparse

from carebot.config import load_config
from carebot.vision.enrollment import Enroller, save_face
from carebot.vision.identity import FaceMatcher
from carebot.vision.pose import open_capture


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", help="resident's name (default: profile.resident_name in the config)")
    ap.add_argument("--webcam", type=int, default=0)
    ap.add_argument("--add", action="store_true", help="add samples to the saved face instead of replacing it")
    ap.add_argument("--config", default="config/mock.yaml")
    args = ap.parse_args()

    import cv2

    cfg = load_config(args.config)
    name = args.name or cfg.get("profile", {}).get("resident_name", "Sandy")
    matcher = FaceMatcher({**cfg["detectors"].get("identity", {}), "name": name})
    mode = "add" if args.add and matcher.ready else "new"
    if args.add and not matcher.ready:
        print("No saved face to add to: saving a new one instead.")
    enroller = Enroller(matcher, mode=mode)
    cap, _ = open_capture(args.webcam)
    print(f"{'Recalibrating' if mode == 'add' else 'Saving a new face for'} {name}. Press q to cancel.")

    while not enroller.done:
        ok, raw = cap.read()
        if not ok:
            break
        shown, msg, good = enroller.step(raw)
        cv2.rectangle(shown, (0, 0), (shown.shape[1], 50), (0, 0, 0), -1)
        cv2.putText(shown, msg, (15, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                    (80, 200, 80) if good else (0, 200, 255), 2, cv2.LINE_AA)
        cv2.imshow("CareBot: save face (q to cancel)", shown)
        if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
            print("Cancelled. Nothing saved.")
            break

    cap.release()
    cv2.destroyAllWindows()
    if enroller.done:
        total = save_face(matcher.path, name if mode == "new" else matcher.name, enroller.samples, mode)
        print(f"Saved. {name}'s face now has {total} samples in {matcher.path} (this machine only).")


if __name__ == "__main__":
    main()
