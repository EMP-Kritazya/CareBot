# Notes: Software → Hardware

Only the software side (Kritazya) writes here. Newest at the bottom, under a timestamp heading.

## 2026-11-07, hour 0: the contract is pushed, you can start

- `carebot/hw/base.py` is the interface, exactly as in `docs/HARDWARE.md`.
- `carebot/hw/mock.py` shows the expected behavior of each part.
- `carebot/hw/__init__.py` has `make_hardware("pi", cfg)`. Use it in `apps/hwcheck.py`. If one of
  your drivers fails in `__init__`, only that part falls back to its mock (and the error is logged).
- `config/hardware_pi.yaml` is seeded from the doc's template and is yours now.
- `tests/test_hw_contract.py` imports each `*_pi.py` on a laptop and checks it against `base.py`
  (subclass, every method implemented, same signatures, `__init__(self, cfg)`). Run
  `python -m pytest` on a laptop before each push; if that test fails, the contract is broken.
- Setup: `uv venv --python 3.11 .venv && uv pip install --python .venv -r requirements.txt`
  (on the Pi: `python3.11 -m venv --system-site-packages .venv`, then pip install both requirements files).

## 2026-11-08: back to the Logitech USB webcam (replaces the Pi camera note below)

- The robot's camera is the **Logitech USB webcam** again, not the ribbon-cable module. Plug it into a
  **blue USB 3 port** on the Pi 5. No `picamera2` needed.
- Software opens it through OpenCV / V4L2 with MJPG at 1280x720 and finds it by name
  (`config/pi.yaml`: `camera.source: usb`), because on the Pi 5 `/dev/video0` can be a SoC device.
  Your `config/hardware_pi.yaml` `camera.index: 0` is kept as a fallback; `rotate` there still works.
- Please check: `v4l2-ctl --list-devices` shows the Logitech, and tell me the model (C920 / C922 / C270 /
  Brio): it sets `camera.fov_deg`, which head-follow uses.
- Mount: the webcam's clip or a 1/4" screw on the pan-tilt bracket, cable with slack for the full pan
  range. It's heavier than the camera module: check the SG90s can still hold the tilt angle.
- Its built-in mic is now the fallback until the INMP441 works (`audio.input` / `voice.input_device`).

## 2026-11-07, evening: the robot uses the Raspberry Pi camera (superseded, see above)

- We're using a **Raspberry Pi camera module on the ribbon cable** (not USB). The software reads it
  with `picamera2` (`carebot/vision/camera.py`), so on the Pi please:
  `sudo apt install python3-picamera2` and create the venv with `--system-site-packages`.
- Check it works: `rpicam-hello -t 5000` (or `libcamera-hello` on older images).
- Tell me which module it is (Camera Module 3 standard / wide, or Module 2): it sets `camera.fov_deg`
  in `config/pi.yaml`, which the head-follow uses. Also whether the head mounts it rotated
  (`camera.rotate`).
- Head follow pans only; tilt stays at the home angle (set `pantilt.home_deg` so the floor in front
  of the bed/chair is in view). Please mount it so pan has the full range.
- Run on the robot: `python -m apps.watcher --config config/pi.yaml --headless`, then open
  `http://carebot.local:8000` for the live view (skeletons only; `--view camera` while aiming it).

## 2026-11-08: reviewed and merged your drivers (commit 32badf8)

- Merged into main. All 4 drivers, `hwcheck`, wiring and tests look good; your tests pass on a laptop
  (34 passed, 2 skipped) and the whole suite is green with them (304).
- Done from your requests: `base.py` / `docs/HARDWARE.md` say Hitec D625MW instead of SG90;
  `MockPanTilt` defaults to pan -60..60.
- The app now accepts your camera config as written: `size: [1280, 720]` (list) and
  `type: csi` -> picamera. MJPG is set before the size (your 23:58 note), with a 1-frame buffer.
  `config/pi.yaml` uses `camera.source: usb` (finds the Logitech by name); your `index` is the fallback.
- Runbook `docs/INTEGRATION.md` step 6 is now one part at a time with your hwcheck commands
  (button, buzzer, eyes, pan-tilt with the power checklist, camera). Venv keeps
  `--system-site-packages` for gpiozero/lgpio, as your requirements-pi.txt says.
- Still needed from you: the Logitech model (sets `camera.fov_deg`), and the calibrated pan-tilt numbers.
