# CareBot integration runbook

For the 4–5 hour integration, once the robot is assembled. Work top to bottom. Each step has a
**check** (what you should see) and **if it fails** (the usual fix). Don't move on until the check passes.

People: **Software** (Kritazya), **Hardware** (drivers, wiring), **Designer** (mounting, the set).
Commands run on the Pi over SSH unless they say *(laptop)*.

## Timeline at a glance

| Time | Steps | Who |
| --- | --- | --- |
| 0:00–0:45 | 0–3: SD card, network, packages, code | Hardware + Software |
| 0:45–1:30 | 4–5: camera, speed | Software |
| 0:45–1:30 | 6: drivers and `hwcheck` (in parallel) | Hardware |
| 1:30–2:15 | 7–8: speaker, mic, face | Software + Hardware |
| 2:15–3:15 | 9–10: head follow, tuning on the real set | Everyone |
| 3:15–4:15 | 11: full demo run, all stories | Everyone |
| 4:15– | 12: start on boot, tag `v-demo` | Software |

---

## 0. Before you start *(laptop)*

```bash
git pull && .venv/bin/python -m pytest -q        # everything green
ls models/                                       # yolo11n-pose_*_ncnn_model, face models, vosk model
```
**Check:** tests pass, and `models/` holds the three `yolo11n-pose_*_ncnn_model` folders, the two
face `.onnx` files and `vosk-model-small-en-us-0.15`. These are git-ignored, so you copy them over in step 3.

## 1. SD card and network *(Hardware)*

- Flash **Raspberry Pi OS (64-bit, Bookworm)** with Raspberry Pi Imager: hostname `carebot`, enable
  SSH, and set the Wi-Fi to **our travel router**, not the venue Wi-Fi.
- Boot, then from the laptop run `ssh <user>@carebot.local`.

**Check:** you're logged in. `python3 --version` shows 3.11.
**If it fails:** `carebot.local` doesn't resolve, so find the Pi's IP on the router's admin page and use that.

## 2. System packages

```bash
sudo apt update
sudo apt install -y python3-picamera2 python3-venv espeak-ng libportaudio2 git
```
**Check:** `rpicam-still -o test.jpg` takes a photo (open it on the laptop with `scp`).
**If it fails:** the camera ribbon is reseated or reversed. Power off, reseat it (contacts toward the
connector's contacts), and boot again. On older images the command is `libcamera-still`.

## 3. Code, packages, models

```bash
git clone <repo-url> CareBot && cd CareBot
python3 -m venv --system-site-packages .venv      # system site packages: picamera2 comes from apt
.venv/bin/pip install -r requirements.txt -r requirements-pi.txt
```
*(laptop)* copy the models, the secrets and, optionally, a saved face:
```bash
scp -r models .env <user>@carebot.local:~/CareBot/
scp -r data/faces <user>@carebot.local:~/CareBot/data/     # optional: re-enroll on the robot instead (step 8)
```
**Check:** `.venv/bin/python -m pytest -q` is green on the Pi (the hardware tests are skipped here).
**If it fails:**
- **numpy errors with picamera2** ("dtype size changed"): pip installed a numpy too new for the apt
  picamera2. Run `.venv/bin/pip install "numpy<2"`.
- **pip runs very slowly or fails on torch:** let it finish (torch is big on ARM), or try
  `.venv/bin/pip install --prefer-binary ultralytics`.

## 4. Camera: aim it

```bash
.venv/bin/python -m apps.vision_live --config config/pi.yaml --headless --view camera
```
Open **http://carebot.local:8000** on the laptop. This is the *camera* view, for setup only.

**Check:** you see the room live, and a skeleton appears when someone walks in.
**Set in `config/pi.yaml` (`camera:`):**
- `rotate`, if the picture is sideways or upside down (0 / 90 / 180 / 270);
- `fov_deg` for the module: Camera Module 3 `[66, 41]`, Module 3 Wide `[102, 67]`, Module 2 `[62, 49]`.

**Aim it:** standing in the middle of the area, a person should be fully in frame with margin, **and the
floor in front of the bed or chair must be visible**. Falls end on the floor.

## 5. Speed: pick the model size

```bash
.venv/bin/python -m apps.bench_pose --camera picamera
```
**Check:** a table of ms per frame and fps for each model. Pick the **largest size at 8 fps or more** and set it
in `config/pi.yaml`:
```yaml
pose:
  model: models/yolo11n-pose_416_ncnn_model   # or _640_ / _320_
  imgsz: 416                                  # must match the model
```
**If everything is under 8 fps:** use `_320_`, and check that the Pi isn't throttling
(`vcgencmd get_throttled` should be `0x0`; it needs the 27 W supply and a heatsink or fan).
If the NCNN models won't load, re-export on the Pi with `.venv/bin/python -m apps.bench_pose --export`.

## 6. Hardware drivers *(Hardware, in parallel with 4–5)*

```bash
.venv/bin/python -m apps.hwcheck --all
.venv/bin/python -m pytest -m pi
```
**Check:** every part says PASS. The servos sweep, the eyes cycle through every state, the buzzer chirps and
then alarms, and button presses print.
**If it fails:**
- See `docs/HARDWARE.md` → "Pi 5 gotchas": the PWM chip number, `config.txt` overlays, and power.
- A part that fails to start falls back to its mock and logs the error. The robot still runs; you just won't
  see that part.

## 7. Speaker and microphone

**Speaker (JBL over Bluetooth):**
```bash
bluetoothctl            # then: scan on / pair <MAC> / trust <MAC> / connect <MAC> / quit
wpctl status            # find the JBL sink id;  wpctl set-default <id>
espeak-ng "CareBot speaker test"
```
**Mic:** `arecord -l` should list the INMP441, or the webcam mic until it arrives.
```bash
.venv/bin/python -m apps.watcher --config config/pi.yaml --list-audio
```
Set `voice.input_device` / `voice.output_device` in `config/pi.yaml` if the defaults are wrong.

**Check:** the JBL speaks the test line, and `--list-audio` shows the mic.
**If it fails:** the JBL disconnects when idle, so turn off its auto-off and add the reconnect unit from `setup/`.

## 8. Sandy's face (on the robot's camera)

```bash
.venv/bin/python -m apps.vision_live --config config/pi.yaml --headless --view camera --save-face
```
Type **`n`** + Enter for a new face, then stand 0.5–1.5 m from the robot, alone, and watch the live view.
Then type **`e`**, **`a`** + Enter and stand where Sandy usually is, to add samples from that distance.

**Check:** the live view shows a green face box labeled `Sandy 0.6` or so, and your friend gets a blue `not Sandy` box.
**If it fails:** the scores are low at a distance, so add more samples there (`e`, `a`), with the room well lit.

## 9. Head follow

```bash
.venv/bin/python -m apps.watcher --config config/pi.yaml --headless --demo --no-ai
```
Walk slowly left and right across the room.

**Check:** the head pans to keep you in frame without twitching, and the tilt doesn't move. When you
leave the room, it goes home after about 3 s.
**Tune (`config/pi.yaml`):**
- `follow.deadband`: raise it if the head twitches.
- `follow.max_step_deg`: lower it if the head lurches.
- `pantilt.home_deg` *(hardware config)*: the tilt that shows the floor.
- If the head pans the wrong way, set `pan.invert: true` in `config/hardware_pi.yaml`.

## 10. Tune on the real set

```bash
.venv/bin/python -m apps.vision_live --config config/pi.yaml --headless --demo
```
Then go through these with the crash mat, the sofa and the chair:

| Do | Expect |
| --- | --- |
| Fall onto the mat, stay down | "FALL DETECTED" 5 s after landing (demo hold) |
| Fall, get up within 5 s | "Got back up: no alert" |
| Sit down hard; lie down slowly | Nothing |
| Slump in the chair, hold still | Stillness after 5 s |
| Friend in view | Blue skeleton labeled "visitor" |

If a fall is missed, lower `fall.drop_torso` or `fall.torso_deg` in `config/detectors.yaml`. If sitting
down triggers it, raise them. Type `r` + Enter to record clips for the accuracy numbers.

## 11. Full demo run

```bash
.venv/bin/python -m apps.watcher --config config/pi.yaml --headless --demo --sim-neighbor 20
```
Live view at **http://carebot.local:8000** (skeleton view). A teammate on the SSH terminal types
`b` / `y` / `d Is he breathing?`. Run every story in PLAN.md:

- [ ] **Story 2:** false alarm. Fall, then "I'm OK" → nothing sent.
- [ ] **Story 1:** fall and stay silent → alert in about 15 s → Krit YES → comfort window ("my hip hurts" is relayed) → `d` questions → button → "taking him to the hospital" → closes.
- [ ] **Story 3:** slumped in the chair → "help" → alert.
- [ ] **Story 4:** the visitor falls → no name used.
- [ ] **Story 5:** fall, get up during the asks, walk away → family-only text.
- [ ] Eyes, buzzer and button do the right thing at each step, and the head is frozen during incidents.
- [ ] Gemini on (`AI: Gemini … ready` at startup), or knowingly off (`--no-ai`).

## 12. Start on boot (optional) and tag

`setup/carebot.service` *(Hardware owns `setup/`)*:
```ini
[Unit]
Description=CareBot
After=network-online.target bluetooth.target

[Service]
WorkingDirectory=/home/<user>/CareBot
ExecStart=/home/<user>/CareBot/.venv/bin/python -m apps.watcher --config config/pi.yaml --headless --demo
Restart=on-failure
User=<user>

[Install]
WantedBy=multi-user.target
```
```bash
sudo cp setup/carebot.service /etc/systemd/system/ && sudo systemctl enable --now carebot
journalctl -u carebot -f      # the narration you'd normally see in the terminal
```
With the service running, keys can't be typed; use `--sim-neighbor`, or stop it and run by hand.
Finally *(laptop)*: commit, push and tag `v-demo`.

## Quick troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| "Could not open camera" / picamera2 import error | Not apt-installed, or the venv lacks system packages | Step 2, and recreate the venv with `--system-site-packages` |
| Live view page doesn't load | Wrong address, or not started with `--headless` | `http://<pi-ip>:8000`; check the startup line "Live view: …" |
| Under 8 fps | Model too big, or throttling | Step 5: smaller model; `vcgencmd get_throttled` |
| CareBot doesn't hear answers | Wrong mic, or answering before the tick | `--list-audio`, set `voice.input_device`; speak after the tick |
| Hears nothing; no speech | JBL not the default sink | `wpctl set-default`; `espeak-ng test` |
| Friend labeled Sandy | Weak enrollment | Step 8: re-save up close, then add samples at a distance |
| Head pans the wrong way | Servo orientation | `pan.invert: true` |
| Falls missed | Floor not in view, or thresholds | Re-aim (step 4), tune (step 10) |
| `AI: Gemini not reachable` | Bad key, or no internet on the router | New key in `.env`; check the router's uplink |
