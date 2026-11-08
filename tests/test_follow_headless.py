"""Head follow (pure), camera sources, the browser live view, and a headless run."""

import urllib.request

import pytest

from carebot.vision.camera import parse_source
from carebot.vision.follow import Follower, target_point
from carebot.vision.synth import person


# ---------------------------------------------------------------- follow

def test_pans_toward_him_when_off_center():
    f = Follower(fov_deg=(66, 41))
    cmd = f.update(0.0, (0.8, 0.5), (0, 0), frozen=False)
    assert cmd[0] == "point" and cmd[1] > 0 and cmd[2] == 0  # right of center -> pan right; tilt untouched


def test_no_move_inside_the_deadband():
    assert Follower().update(0.0, (0.55, 0.5), (0, 0), frozen=False) is None


def test_mirrored_preview_flips_the_direction():
    cmd = Follower().update(0.0, (0.8, 0.5), (0, 0), frozen=False, mirrored=True)
    assert cmd[1] < 0


def test_steps_are_limited_and_rate_limited():
    f = Follower(max_step_deg=4, rate_hz=8)
    cmd = f.update(0.0, (1.0, 0.5), (0, 0), frozen=False)
    assert cmd[1] == pytest.approx(4)
    assert f.update(0.05, (1.0, 0.5), (4, 0), frozen=False) is None  # too soon
    assert f.update(0.2, (1.0, 0.5), (4, 0), frozen=False)[1] == pytest.approx(8)


def test_frozen_never_moves():
    assert Follower().update(0.0, (0.95, 0.5), (0, 0), frozen=True) is None


def test_tilt_stays_put_by_default_even_far_off():
    cmd = Follower().update(0.0, (0.9, 0.05), (0, 0), frozen=False)
    assert cmd[2] == 0


def test_goes_home_after_losing_him():
    f = Follower(home_after_s=3)
    f.update(0.0, (0.9, 0.5), (0, 0), frozen=False)       # was following
    assert f.update(1.0, None, (4, 0), frozen=False) is None
    assert f.update(3.5, None, (4, 0), frozen=False) == ("home",)
    assert f.update(4.0, None, (0, 0), frozen=False) is None  # only once


def test_target_point_is_the_torso_middle():
    x, y = target_point(person(1, (0.3, 0.6)))
    assert x == pytest.approx(0.3, abs=0.02) and y < 0.6  # between shoulders and hips


# ---------------------------------------------------------------- camera sources

@pytest.mark.parametrize("spec,expected", [("picamera", "picamera"), ("pi", "picamera"), ("1", 1), (2, 2),
                                           (None, 0), ("clip.mp4", "clip.mp4"), ("usb", "usb"), ("Logitech", "usb"),
                                           ("/dev/video2", "/dev/video2")])
def test_parse_source(spec, expected):
    assert parse_source(spec) == expected


# ---------------------------------------------------------------- browser view + headless run

def _blank_video(tmp_path, frames=8):
    import cv2
    import numpy as np

    path = str(tmp_path / "blank.mp4")
    w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), 10, (320, 240))
    for _ in range(frames):
        w.write(np.full((240, 320, 3), 120, np.uint8))
    w.release()
    return path


def test_mjpeg_server_serves_the_latest_frame():
    import numpy as np

    from carebot.vision.mjpeg import MjpegServer

    server = MjpegServer(port=0, host="127.0.0.1")
    try:
        server.publish(np.zeros((60, 80, 3), np.uint8))
        page = urllib.request.urlopen(f"http://127.0.0.1:{server.port}/", timeout=3).read()
        assert b"/stream" in page
        server.publish(np.zeros((60, 80, 3), np.uint8))
        jpeg = urllib.request.urlopen(f"http://127.0.0.1:{server.port}/snapshot", timeout=3).read()
        assert jpeg[:2] == b"\xff\xd8"  # a JPEG
    finally:
        server.close()


def test_headless_skeleton_view_never_shows_the_room(tmp_path):
    from carebot.config import load_config
    from carebot.vision.live import LiveVision

    cfg = load_config("config/mock.yaml")
    vision = LiveVision(cfg, window="t", video=_blank_video(tmp_path), headless=True, http_port=None)
    try:
        assert vision.view == "skeleton" and vision.mirror is False
        tick = vision.step()
        assert not tick.face_saving
        assert tick.image.max() == 0, "skeleton view must not contain any camera pixels"
        assert vision.show(tick.image) == 255  # no window; no key typed
        vision.press("e")
        assert vision.show(tick.image) == ord("e")
    finally:
        vision.close()



def test_finds_the_logitech_among_the_pi5_video_devices(tmp_path):
    """Pi 5: /dev/video0.. are often the SoC's ISP/codec nodes; the webcam is the USB one (index 0)."""
    from carebot.vision.camera import find_usb_camera
    def node(n, name, bus, index="0"):
        d = tmp_path / "class" / f"video{n}"
        d.mkdir(parents=True)
        (d / "name").write_text(name + "\n")
        (d / "index").write_text(index + "\n")
        target = tmp_path / "devices" / bus / f"video{n}"
        target.mkdir(parents=True)
        (d / "device").symlink_to(target)
    node(0, "pispbe", "platform/1000880000.pisp_be")
    node(19, "rpivid", "platform/rpivid")
    node(21, "HD Pro Webcam C920", "platform/xhci/usb3/3-1/3-1:1.0")
    node(22, "HD Pro Webcam C920", "platform/xhci/usb3/3-1/3-1:1.0", index="1")   # metadata node
    assert find_usb_camera(str(tmp_path / "class")) == "/dev/video21"


def test_no_usb_camera_found(tmp_path):
    from carebot.vision.camera import find_usb_camera
    assert find_usb_camera(str(tmp_path / "nothing")) is None


def test_a_device_path_is_a_camera_not_a_video_file():
    from carebot.vision.camera import is_device
    assert is_device("/dev/video0") and is_device(0) and not is_device("clip.mp4")
