# Notes: Hardware → Software

Only the hardware teammate writes here. Newest at the bottom, under a timestamp heading.

## 2026-11-07 23:15: servos changed to Hitec D625MW; PiPanTilt written

- **The head now uses two Hitec D625MW servos, not SG90s,** powered by a separate 6 V battery
  (grounds tied to the Pi). Wiring and power are in `docs/wiring.md`.
- `carebot/hw/pantilt_pi.py` (`PiPanTilt`) implements `PanTilt` exactly as in `base.py`. It passes
  `tests/test_hw_contract.py`, and `tests/hw/test_pantilt_pi.py` covers it on a laptop with a fake
  PWM library. Not yet run on the robot: next step is `python -m apps.hwcheck pantilt`.
- `config/hardware_pi.yaml` pantilt changes:
  - pulse range 900 to 2100 us (Hitec), with two new optional keys per axis: `center_us` and
    `us_per_deg`. Without them the driver keeps the old SG90 mapping (range spans 180 degrees).
  - **pan limit is now -60..60 degrees** (was -80..80) until calibrated, because 900..2100 us at
    the first-guess 10 us/deg only reaches 60. Values outside are clamped, as the contract says.
  - settle_ms stays 250 for now.
- Requests for software (files I don't own):
  - `base.py` `is_moving` docstring and `docs/HARDWARE.md` still say SG90; the time-based
    behavior is unchanged, so it's wording only.
  - `MockPanTilt` defaults to pan -80..80 when no config is given; consider -60..60 to match.
- `close()` stops PWM, so the servos go limp. They hold position at all other times.

## 2026-11-07 23:35: the camera is a ribbon-cable Pi camera, not a USB webcam

- It's on the Pi 5's CAM/DISP 1 connector. On the Pi 5 it only works through libcamera
  (`picamera2`, installed with apt), so **`cv2.VideoCapture(0)` in `open_capture`
  (`carebot/vision/pose.py`) won't see it.**
- `config/hardware_pi.yaml` `camera:` now has `type: csi | usb`, `csi_num` and `size`.
  `python -m apps.hwcheck camera` handles both types; see `_grab_csi` there for working
  picamera2 code: `create_video_configuration(main={"size": (w, h), "format": "RGB888"})`, then
  `capture_array()` gives BGR frames that OpenCV and YOLO take as they are.
- Request: when `camera.type` is `csi`, have the live pose source read frames from picamera2.
  The USB path and video files stay as they are, so the laptop is unaffected.

## 2026-11-07 23:42: back to the Logitech USB webcam (ignore the CSI request above)

- We're using the Logitech USB webcam after all, so `open_capture` needs no change.
  `camera.type` is `usb` in `config/hardware_pi.yaml`; the CSI support stays in `apps/hwcheck.py`
  only, in case we ever switch.

## 2026-11-07 23:58: webcam size and format (please apply in open_capture)

- The Logitech's widest view is its 16:9 modes. `config/hardware_pi.yaml` now has
  `camera.size: [1280, 720]` and `camera.fourcc: MJPG`.
- **MJPG is required:** `v4l2-ctl --list-formats-ext` shows every size above 640x360 at only
  **5 fps in YUYV**, but 30 fps in MJPG up to 1920x1080. OpenCV picks YUYV unless asked.
- Request: when opening a live webcam on the robot, set the format before the size:
  `cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))`, then width and height from
  `camera.size`. `_set_fourcc` in `apps/hwcheck.py` does exactly this.
- Bench live view (until the app's MJPEG view exists):
  `python -m apps.hwcheck camera --live`, opened through `ssh -L 8000:localhost:8000`.

## 2026-11-08 00:10: PiButton, PiBuzzer, PiEyes written

- `button_pi.py`, `buzzer_pi.py`, `eyes_pi.py` implement `base.py` exactly; all four Pi drivers now
  pass `tests/test_hw_contract.py`, and `tests/hw/` covers them on a laptop with fake pin libraries.
  Not yet run on the robot: next is `python -m apps.hwcheck button | buzzer | eyes`.
- The eye looks follow the table in `docs/HARDWARE.md` (`render()` in `eyes_pi.py`).
  Unknown states show `error` instead of raising, like the mock.
- The button callback runs on gpiozero's thread, as the contract says. Exceptions from it are
  logged, not raised.
- Full pin map and power in `docs/wiring.md`. Needs `dtparam=spi=on` for the eyes.

## 2026-11-08 05:45: final integration review: setup/ is in, three runbook fixes

Reviewed main at 02bdd6a. The app drives the hardware correctly (button -> queue, eyes and
buzzer from the state machine, head frozen during incidents, `hw.close()` on exit), all eye states
the state machine uses exist in the driver, and 303/304 tests pass on Python 3.11 (the one
failure needs ultralytics, which my test machine doesn't have). `apps.simulate` runs every story.

- **New `setup/`** (mine): `carebot.service`, `jbl-reconnect@.service` + `jbl-reconnect.sh`,
  `config.txt.snippet` + `add-config.sh`. `.gitattributes` keeps `*.sh` and `*.service` on LF
  line endings; with CRLF from a Windows checkout they fail on the Pi.
- Please update `docs/INTEGRATION.md`:
  1. **Step 6a:** the mic also needs `dtparam=i2s=on` and `dtoverlay=adau7002-simple`, or the
     INMP441 never shows up in step 7. Easiest: `sudo bash setup/add-config.sh` adds all four lines.
  2. **Step 12:** run CareBot as a systemd **user** service (`setup/carebot.service`, install steps
     inside), not a system service with `User=`. A system service usually can't reach PipeWire, so
     the robot would run but never speak.
  3. **Step 3:** the Pi's venv currently lives at `~/carebot-venv`; I'm linking it as
     `~/CareBot/.venv` so every `.venv/bin/python` command works as written.
- Logitech model: its MJPG list tops out at 1920x1080, so it's C920 class, not a C270, and
  `fov_deg: [70, 43]` fits. Confirming the exact name with `v4l2-ctl --list-devices` on the Pi.
