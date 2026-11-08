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
sudo apt install -y python3-venv python3-gpiozero python3-lgpio espeak-ng libportaudio2 git v4l-utils \
                    mosquitto mosquitto-clients
# The robot is the map's MQTT broker. Mosquitto 2 only listens to the Pi itself by default; open it on our router:
printf 'listener 1883\nallow_anonymous true\n' | sudo tee /etc/mosquitto/conf.d/carebot.conf
sudo systemctl enable --now mosquitto && sudo systemctl restart mosquitto
```
**Check (MQTT):** on the laptop, `mosquitto_sub -h carebot.local -t 'carebot/#' -v` connects (it prints
the robot's heartbeat once the watcher runs). Only JSON goes over it; it's our own router, no internet.
**Check (camera):** the Logitech is plugged into a **blue USB 3 port** (more power and bandwidth), then
```bash
v4l2-ctl --list-devices                      # the Logitech is listed under a usb-... line (e.g. /dev/video0)
v4l2-ctl -d /dev/video0 --list-formats-ext   # shows MJPG with 1280x720 at 30 fps
```
On the Pi 5 the SoC's own video devices (`pispbe`, `rpivid`, ...) are listed too; that's why
`config/pi.yaml` says `camera.source: usb` (it finds the webcam by name). Your user must be in the
`video` group (`groups`; if not: `sudo usermod -aG video $USER`, log out and in).
**If it fails:** replug it, try the other blue port, `dmesg | tail` should show "uvcvideo". If `dmesg`
shows the webcam resetting under load, use a powered USB hub.

## 3. Code, packages, models

```bash
git clone <repo-url> CareBot && cd CareBot
python3 -m venv --system-site-packages .venv      # gpiozero + lgpio (button, buzzer) come from apt
.venv/bin/pip install -r requirements.txt -r requirements-pi.txt
```
*(laptop)* copy the models, the secrets and, optionally, a saved face:
```bash
scp -r models .env <user>@carebot.local:~/CareBot/
scp -r data/faces <user>@carebot.local:~/CareBot/data/     # optional: re-enroll on the robot instead (step 8)
```
**Check:** `.venv/bin/python -m pytest -q` is green on the Pi (the hardware tests are skipped here).
**If it fails:**
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
- `fov_deg` for the Logitech (head-follow uses it): C920 / C922 / Brio-class `[70, 43]` (the default),
  C270 `[48, 28]`. Not sure? Stand 2 m away and measure how wide the visible area is (W m):
  horizontal FOV = 2 * atan(W / 4) in degrees.
- `mirror: false` stays: the robot's view is never mirrored (only a laptop preview is).
- Autofocus hunting (picture pulses in and out)? Fix the focus: `v4l2-ctl -d /dev/video0 -c focus_automatic_continuous=0 -c focus_absolute=0`
  (older drivers call it `focus_auto`).

**Aim it:** standing in the middle of the area, a person should be fully in frame with margin, **and the
floor in front of the bed or chair must be visible**. Falls end on the floor.

## 5. Speed: pick the model size

```bash
.venv/bin/python -m apps.bench_pose --camera usb
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

## 6. Hardware drivers, one part at a time *(Hardware + Software together)*

Wiring and power: `docs/wiring.md`. Do the parts in this order; each step has its own check.

**6a. Pi settings (once).** At the end of `/boot/firmware/config.txt`, then `sudo reboot`:
```
dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4
dtparam=spi=on
```
**Check:** `ls /sys/class/pwm` shows a `pwmchip*` (its number goes in `pantilt.pwm_chip`), and
`ls /dev/spidev0.0` exists.

**6b. Button** (no power risk, so first): `.venv/bin/python -m apps.hwcheck button`, press it a few times.
**Check:** one line per press, never two for one press. Nothing? Plain button: `pull_up: true`;
Grove module: `pull_up: false`.

**6c. Buzzer:** `.venv/bin/python -m apps.hwcheck buzzer`. **Check:** one short chirp, then the alarm for
3 s, then silence. Only clicks? `buzzer.type: passive`.

**6d. Eyes:** `.venv/bin/python -m apps.hwcheck eyes`. **Check:** each state in turn: blue breathing
(watching), amber pulse (checking), red flash (alerted), green (help coming), purple (error).
Wrong colors: `eyes.pixel_order`; flicker: use the level shifter; set `eyes.num_pixels` to the LED count.

**6e. Pan-tilt (careful).** Before powering the servos: battery under 7.4 V, battery minus tied to Pi
pin 34, nothing from the battery on the Pi's 5 V pins, and the head free to move. Connect the servo
battery **last**. Then `.venv/bin/python -m apps.hwcheck pantilt` and follow its calibration help.
**Check:** the sweep stays inside `limit_deg`; `a 30 0` turns the camera toward the **right of its image**
(else `pan.invert: true`); `a 0 20` tilts it **up** (else `tilt.invert: true`); no servo buzzes against a
stop. Commit the calibrated numbers in `config/hardware_pi.yaml`.

**6f. Camera through the hardware check:** `.venv/bin/python -m apps.hwcheck camera`. **Check:** about
30 fps at 1280x720 (MJPG); `/tmp/hwcheck.jpg` shows the room.

**6g. All together:** `.venv/bin/python -m apps.hwcheck --all`, then `.venv/bin/python -m pytest -q`.
**Check:** every part PASS; tests green.
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
**Mic:** `arecord -l` should list the INMP441, or the Logitech's own mic (a "C920"-style card) until it arrives.
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

**Check:** the head pans to keep you in frame without twitching, and it tilts only after you've
stayed high or low in the picture for about 1.5 s (sit down: it tilts down a moment later). When you
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
.venv/bin/python -m apps.watcher --config config/pi.yaml --headless --demo      # the robot (SSH)
python -m dashboard.server --mqtt carebot.local                                 # the laptop: the map
```
Response console at **http://localhost:8050** on the laptop. **Live** mode follows the robot's camera;
**Manual** mode: click Sandy's home, then *Send alert* (the robot's real pipeline: check-in, then
911 + Suyog + nearest neighbors). It simulates the neighbors without a phone; real phones are rung. Live view at **http://carebot.local:8000** (skeleton view).
If the map says "waiting", check the MQTT line in step 2. A teammate on the SSH terminal types
`b` / `y` / `d Is he breathing?`. Run every story in PLAN.md:

- [ ] **Story 2:** false alarm. Fall, then "I'm OK" → nothing sent.
- [ ] **Story 1:** fall and stay silent → alert in about 15 s → Krit YES → comfort window ("my hip hurts" is relayed) → `d` questions → button → "taking him to the hospital" → closes.
- [ ] **Story 3:** slumped in the chair → "help" → alert.
- [ ] **Story 4:** the visitor falls → no name used.
- [ ] **Story 5:** fall, get up during the asks, walk away → family-only text.
- [ ] Eyes, buzzer and button do the right thing at each step, and the head is frozen during incidents.
- [ ] Claude on (`AI: Claude claude-opus-5-5 ready` at startup), or knowingly off (`--no-ai`).

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
| "Could not open camera" | Webcam not found, or no permission | Step 2: `v4l2-ctl --list-devices`, `video` group; or set `camera.source: /dev/videoN` |
| Camera at 5-10 fps, laggy | Running uncompressed YUYV, or on a USB 2 port | It asks for MJPG; use a blue USB 3 port; `--list-formats-ext` must show MJPG 1280x720 |
| Live view page doesn't load | Wrong address, or not started with `--headless` | `http://<pi-ip>:8000`; check the startup line "Live view: …" |
| Under 8 fps | Model too big, or throttling | Step 5: smaller model; `vcgencmd get_throttled` |
| CareBot doesn't hear answers | Wrong mic, or answering before the tick | `--list-audio`, set `voice.input_device`; speak after the tick |
| Hears nothing; no speech | JBL not the default sink | `wpctl set-default`; `espeak-ng test` |
| Friend labeled Sandy | Weak enrollment | Step 8: re-save up close, then add samples at a distance |
| Head pans the wrong way | Servo orientation | `pan.invert: true` |
| Falls missed | Floor not in view, or thresholds | Re-aim (step 4), tune (step 10) |
| Map stuck on "waiting · MQTT carebot.local" | Broker not reachable from the laptop | Step 2: `carebot.conf` listener, `sudo systemctl restart mosquitto`; same router |
| `AI: Claude not reachable` | Bad or missing `ANTHROPIC_API_KEY`, or no internet on the router | Check the key in `.env`; check the router's uplink |
