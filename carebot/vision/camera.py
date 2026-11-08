"""Camera sources behind one small interface: read() -> (ok, BGR image), size(), fps, release().

    picamera            Raspberry Pi camera on the ribbon cable (picamera2, Pi 5). Video never
                        leaves the robot: it doesn't even touch a network.
    0, 1, ...           USB webcam index (laptop testing)
    path/to/clip.mp4    a video file (repeatable tests); fps comes from the file

picamera2 is imported lazily (it comes from apt on the Pi), so laptops never need it.
"""

from __future__ import annotations

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
    return int(s) if s.isdigit() else s


class _Rotating:
    def __init__(self, rotate: int) -> None:
        import cv2

        self._cv2 = cv2
        self._rot = getattr(cv2, ROTATIONS[rotate]) if rotate in ROTATIONS else None

    def _turn(self, img):
        return self._cv2.rotate(img, self._rot) if self._rot is not None and img is not None else img


class OpenCVCamera(_Rotating):
    """A USB webcam or a video file through OpenCV."""

    def __init__(self, source: int | str, size: str | None = None, rotate: int = 0) -> None:
        super().__init__(rotate)
        cv2 = self._cv2
        self.cap = cv2.VideoCapture(source)
        if not self.cap.isOpened():
            hint = (" On macOS, allow camera access for your terminal / VS Code in System Settings > "
                    "Privacy & Security > Camera, then restart it.") if isinstance(source, int) else ""
            raise RuntimeError(f"Could not open camera {source!r}.{hint}")
        self.is_file = isinstance(source, str)
        if not self.is_file and size:
            w, h = (int(v) for v in size.lower().split("x"))
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
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


def open_camera(spec: str | int | None, size: str | None = None, rotate: int = 0):
    source = parse_source(spec)
    if source == "picamera":
        return PiCamera(size, rotate)
    return OpenCVCamera(source, size, rotate)
