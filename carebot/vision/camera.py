"""Camera sources behind one small interface: read() -> (ok, BGR image), size(), fps, release().

    usb                 the robot's eye: the first USB webcam (our Logitech), found by name in
                        /sys/class/video4linux on the Pi. Video never leaves the robot.
    0, 1, ... / /dev/videoN   a webcam by index or device path (laptop testing, or a fixed device)
    path/to/clip.mp4    a video file (repeatable tests); fps comes from the file
    picamera            a Raspberry Pi camera module (picamera2), still supported but not used now

On Linux, webcams open through V4L2 with MJPG (keeps 1280x720 at 30 fps over USB) and a 1-frame
buffer, so the detectors always see the newest frame, never a stale one. picamera2 is lazy-imported.
"""

from __future__ import annotations

import glob
import os
import sys

ROTATIONS = {90: "ROTATE_90_CLOCKWISE", 180: "ROTATE_180", 270: "ROTATE_90_COUNTERCLOCKWISE"}


def parse_source(spec: str | int | None) -> str | int:
    """'picamera' | webcam index | file path. Digits mean a webcam index."""
    if spec is None:
        return 0
    if isinstance(spec, int):
        return spec
    s = str(spec).strip()
    if s.lower() in ("picamera", "pi", "csi"):
        return "picamera"
    if s.lower() in ("usb", "logitech", "webcam"):
        return "usb"
    return int(s) if s.isdigit() else s


def parse_size(size) -> tuple[int, int]:
    """'1280x720' (run configs) or [1280, 720] (config/hardware_pi.yaml)."""
    if isinstance(size, (list, tuple)):
        return int(size[0]), int(size[1])
    w, h = str(size).lower().split("x")
    return int(w), int(h)


def is_device(source: str | int) -> bool:
    return isinstance(source, int) or str(source).startswith("/dev/video")


def find_usb_camera(sysfs: str = "/sys/class/video4linux", prefer: tuple[str, ...] = ("logitech", "webcam", "c920",
                    "c922", "c270", "brio", "uvc")) -> str | None:
    """The first USB webcam's capture node, e.g. /dev/video0. On the Pi 5 the SoC's own video
    devices (ISP, codecs) are also /dev/videoN, so pick by USB bus + name, not by number."""
    found = []
    for node in sorted(glob.glob(os.path.join(sysfs, "video*")), key=lambda p: int(p.rsplit("video", 1)[1] or 0)):
        try:
            name = open(os.path.join(node, "name")).read().strip().lower()
            index = open(os.path.join(node, "index")).read().strip() if os.path.exists(os.path.join(node, "index")) else "0"
            real = os.path.realpath(os.path.join(node, "device"))
        except OSError:
            continue
        if index != "0" or "usb" not in real:  # index 1 = the metadata node of the same camera
            continue
        found.append((0 if any(p in name for p in prefer) else 1, "/dev/" + os.path.basename(node)))
    return min(found)[1] if found else None


class _Rotating:
    def __init__(self, rotate: int) -> None:
        import cv2

        self._cv2 = cv2
        self._rot = getattr(cv2, ROTATIONS[rotate]) if rotate in ROTATIONS else None

    def _turn(self, img):
        return self._cv2.rotate(img, self._rot) if self._rot is not None and img is not None else img


class OpenCVCamera(_Rotating):
    """A USB webcam or a video file through OpenCV."""

    def __init__(self, source: int | str, size: str | None = None, rotate: int = 0, fourcc: str | None = "MJPG") -> None:
        super().__init__(rotate)
        cv2 = self._cv2
        self.is_file = not is_device(source)
        linux_device = not self.is_file and sys.platform.startswith("linux")
        self.cap = cv2.VideoCapture(source, cv2.CAP_V4L2) if linux_device else cv2.VideoCapture(source)
        if not self.cap.isOpened():
            hint = (" On macOS, allow camera access for your terminal / VS Code in System Settings > "
                    "Privacy & Security > Camera, then restart it.") if sys.platform == "darwin" and not self.is_file else \
                   (" Is the webcam plugged in? `v4l2-ctl --list-devices`; is your user in the `video` group?"
                    if linux_device else "")
            raise RuntimeError(f"Could not open camera {source!r}.{hint}")
        if linux_device:
            if fourcc:  # MJPG before the size: uncompressed YUYV can't do 720p at 30 fps over USB
                self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # always the newest frame (pose runs slower than 30 fps)
        if not self.is_file and size:
            w, h = parse_size(size)
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        if linux_device:
            self.cap.set(cv2.CAP_PROP_FPS, 30)
        self.fps = (self.cap.get(cv2.CAP_PROP_FPS) or 30.0) if self.is_file else None
        self.name = f"video {source}" if self.is_file else f"webcam {source}"

    def read(self):
        ok, img = self.cap.read()
        return ok, self._turn(img) if ok else None

    def size(self) -> tuple[int, int]:
        w = int(self.cap.get(self._cv2.CAP_PROP_FRAME_WIDTH))
        h = int(self.cap.get(self._cv2.CAP_PROP_FRAME_HEIGHT))
        return (h, w) if self._rot in (self._cv2.ROTATE_90_CLOCKWISE, self._cv2.ROTATE_90_COUNTERCLOCKWISE) else (w, h)

    def release(self) -> None:
        self.cap.release()


class PiCamera(_Rotating):
    """Raspberry Pi camera module through picamera2 (libcamera). Frames come out BGR for OpenCV."""

    def __init__(self, size: str | None = None, rotate: int = 0) -> None:
        super().__init__(rotate)
        try:
            from picamera2 import Picamera2
        except ImportError as e:
            raise RuntimeError("picamera2 not found. On the Pi: sudo apt install python3-picamera2, and create "
                               "the venv with --system-site-packages.") from e
        w, h = (int(v) for v in (size or "1280x720").lower().split("x"))
        self.cam = Picamera2()
        # picamera2's "RGB888" is stored B,G,R in memory: exactly what OpenCV expects.
        self.cam.configure(self.cam.create_video_configuration(main={"size": (w, h), "format": "RGB888"}))
        self.cam.start()
        try:  # Camera Module 3 has autofocus; older modules just ignore this
            from libcamera import controls
            self.cam.set_controls({"AfMode": controls.AfModeEnum.Continuous})
        except Exception:  # noqa: BLE001
            pass
        self._size = (w, h)
        self.fps = None
        self.is_file = False
        self.name = "Raspberry Pi camera"

    def read(self):
        try:
            return True, self._turn(self.cam.capture_array("main"))
        except Exception:  # noqa: BLE001
            return False, None

    def size(self) -> tuple[int, int]:
        w, h = self._size
        return (h, w) if self._rot in (self._cv2.ROTATE_90_CLOCKWISE, self._cv2.ROTATE_90_COUNTERCLOCKWISE) else (w, h)

    def release(self) -> None:
        try:
            self.cam.stop()
            self.cam.close()
        except Exception:  # noqa: BLE001
            pass


def open_camera(spec: str | int | None, size: str | None = None, rotate: int = 0, fourcc: str | None = "MJPG"):
    source = parse_source(spec)
    if source == "picamera":
        return PiCamera(size, rotate)
    if source == "usb":
        found = find_usb_camera() if sys.platform.startswith("linux") else None
        source = found if found else 0  # laptops: the first webcam
        print(f"USB webcam: {source}", flush=True)
    return OpenCVCamera(source, size, rotate, fourcc)
