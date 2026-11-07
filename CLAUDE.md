# CareBot: project context for Claude

MakeCU 24-hour robotics hackathon, Healthcare track. We are building **one robot** that watches
over an older adult living alone. It detects a fall or stillness, checks in by voice, and if nobody
answers, alerts the nearest neighbors who agreed to help, calls the family, and briefs "911".
A laptop map shows it all. PLAN.md is the source of truth for stories, rules and the demo.

## Hard rules

- **All code is written during the hackathon.** Do not copy pre-event code into this repo.
  The old `carecam` scaffold is reference only. Credit every library and dataset in the README.
- **No medical guidance.** The device alerts a human. Never output diagnoses, vitals
  interpretation, or health advice.
- **The 911 call is a stand-in.** It dials a teammate's phone. Never dial real 911.
- **Honesty:** the 911 agent and every update speak only from the code-built fact sheet and verbatim
  quotes. Unknown means "I don't know." Ruth hears "someone is coming" only after a person says YES.
- **Privacy:** video and face embeddings never leave the robot. Only JSON events go over the network.
- **Field of view:** act only on what the camera sees. Out of view is not an emergency.
- **It is a robotics project.** The camera sits on a pan-tilt servo head that follows the resident.
- Never log or commit secrets. Credentials live in `.env` (git-ignored). Keep `.env.example` current.

## System

- **The robot:** Raspberry Pi 5 (4GB). There is **no AI Camera (IMX500)**; it is out of scope.
  - Pose: Logitech USB webcam with YOLO11n-pose and ByteTrack (17 COCO keypoints, multi-person).
    The fallback is MediaPipe Pose, single person.
  - Pan-tilt: two SG90 servos on GPIO 12 (pan) and 13 (tilt), using Pi 5 hardware PWM
    (`dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4`, plus `rpi-hardware-pwm`). They run off a
    separate 5 V supply with a common ground. No PCA9685; pigpio doesn't run on the Pi 5.
  - LED eyes: a NeoPixel arc on SPI GPIO 10 (`adafruit-circuitpython-neopixel-spi`) through a level
    shifter. rpi_ws281x doesn't run on the Pi 5.
  - Buzzer on GPIO 17. The button is on GPIO 27.
  - Speech out: a JBL speaker over Bluetooth (fallback: AUX through a USB sound card). Speech in: an
    INMP441 I2S mic (fallback: the webcam mic).
  - The robot runs the Mosquitto broker.
- **The laptop:** the map dashboard (FastAPI + WebSocket + Leaflet/OSM). It subscribes to the robot
  over MQTT, simulates the receivers that don't have real phones, and has a "Trigger alert" button.
- **No second unit.** The Pi 4 is a spare robot brain only.
- **Link:** MQTT over our own travel router. Topics: `carebot/robot-4A/event`,
  `carebot/robot-4A/cmd`, `carebot/robot-4A/receivers`.
- **Escalation, all fired at once on ALERTED:**
  - Neighbors who agreed to help (receivers in `config/receivers.yaml`), texted in waves of 3,
    nearest first by precomputed walking time (`data/routes.json`).
  - The family, by Twilio SMS and a call.
  - The 911 agent: a TwiML briefing, then Q&A with `claude-haiku-5-5`, answering only from the fact
    sheet.

Both machines are headless or remote. We work over SSH (`ssh <user>@carebot.local`).
The live view is an MJPEG stream on port 8000.

## Architecture

Every hardware part sits behind a small interface with a mock, chosen in `config/*.yaml`
(`hardware: mock | pi`). Everything must run on a laptop with mocks.

| Module | Real | Mock |
| --- | --- | --- |
| pose source | webcam + YOLO11n-pose/ByteTrack (NCNN on Pi), MediaPipe fallback, video file | video file |
| face lock | OpenCV YuNet + SFace, bound to a ByteTrack ID | fixed resident track ID |
| tracker | SG90 servos (hardware PWM) | prints angles |
| eyes / buzzer / button | NeoPixel SPI, GPIO | red terminal banner; type `b` + Enter = button |
| voice | Piper/espeak-ng out, Vosk in (keywords; full transcript for "who's there"; 15 s timeout) | keyboard |
| notifier | Twilio SMS + calls | console |
| 911 agent | Flask TwiML webhook + Claude Q&A + forbidden-term filter | terminal text loop |
| intent | Claude classifies a transcript into the fixed list of statuses | keyword rules |
| receivers | ranking + waves; `sms` (real) or `sim` (dashboard-simulated) | all `sim` |
| transport | MQTT | in-memory |

### Hardware contract

The Pi drivers are built by the hardware teammate (with their own Claude) against
`carebot/hw/base.py`. **Read `docs/HARDWARE.md` before touching anything in `carebot/hw/`,
`config/hardware_pi.yaml`, `setup/` or `apps/hwcheck.py`.**
- **Software owns:** `base.py`, `mock.py`, `hw/__init__.py` (factory).
- **Hardware owns:** `*_pi.py`, `config/hardware_pi.yaml`, `setup/`, `apps/hwcheck.py`,
  `tests/hw/`, `docs/wiring.md`, `requirements-pi.txt`.
- **Changing the interface:** write it in `notes/` first, then change `base.py`, `mock.py` and the
  doc together. Never break the mock.
- **Drivers stay dumb:** no app logic, no network, non-blocking, lazy imports.

### Face lock

Enroll with `apps.enroll` (embeddings in git-ignored `data/faces/`). A face match (cosine at least
0.363) binds to the track whose nose and eye keypoints lie inside the face box; that track becomes
`resident_track_id`. The lock stays with the ByteTrack ID while the face is hidden. If one person is
in view and nobody is locked, assume it's the resident (config flag).

The lock decides who the head follows, whose stillness counts, whose name is spoken, and who pressed
the button. A fall by anyone in view still triggers a check-in, without a name if it isn't Ruth.

### State machine

Pure, no I/O, fully unit tested.

- **MONITORING.** A fall seen and not recovered, resident stillness in view, or the map trigger goes
  to CHECK_IN.
- **CHECK_IN** (2 asks):
  - Voice "I'm OK", or the button followed by voice "I'm OK", goes back to MONITORING.
  - "Help", no answer after 2 asks, or the button followed by 2 asks with no voice goes to ALERTED.
- **ALERTED.** The robot beeps, and neighbors, family and 911 are contacted.
  - A neighbor's YES sets `on_the_way`.
  - The button pressed by someone else (Ruth's track still lying down or out of view) starts "Who
    are you, and what's happening?" The reply sets `with_her`, `hospital`, `she_is_ok` and so on,
    and is sent as a verbatim quote.
  - The button pressed by Ruth plus a spoken "I'm OK" goes to CLOSED.
- **CLOSED.** Back to MONITORING.

The button stops the beeping and pauses escalation. It never closes an alert by itself.

### Messages

```json
{"type": "alert", "sender": "robot-4A", "incident_id": "...", "event": "fall", "who": "resident", "ts": "..."}
{"type": "update", "sender": "robot-4A", "incident_id": "...", "status": "hospital", "quote": "...", "ts": "..."}
{"type": "trigger"}
```

### Detectors

Thresholds live in YAML and are never hard-coded.

- **Fall:** the hip midpoint drops at least 0.5 torso lengths within 1.2 s, AND the torso tips at
  least 45 degrees from upright while dropping (measured in body sizes, so distance doesn't matter;
  a fall onto a sofa counts, lying down slowly doesn't). Then:
  - she stays down for `fall_hold_s` (10 s), OR
  - she leaves the frame and doesn't reappear upright within `fall_out_of_view_s` (10 s).

  Standing back up within the hold means no alert (`fall_recovered`). The servos freeze once a fall
  candidate starts.
- **Stillness:** only the resident, only while in view. Mean keypoint motion stays under the
  threshold for `stillness_s` (30 s demo).
- **Out of view:** do nothing. No missing-person alert. The head returns home and waits.

## Conventions

- Python 3.11 (Raspberry Pi OS Bookworm). Lazy-import hardware and service libraries (`gpiozero`,
  `rpi_hardware_pwm`, `neopixel_spi`, `ultralytics`, `mediapipe`, `paho`, `twilio`, `vosk`,
  `anthropic`) inside the classes that use them, so laptops never need them for the mocks.
- On the Pi, the venv is created with `--system-site-packages`.
- Add or update a pytest test with every detector or state-machine change. Run `python -m pytest` before committing.
- Small commits after each working step, on `main`. Tag `v-h12` at hour 12 and `v-demo` before filming.
- Prefer simple and robust over clever. This is a 24-hour demo.

## Commands

```bash
python -m apps.simulate              # all 4 user stories, scripted, no hardware
python -m apps.watcher --video samples/fall_01.mp4
python -m apps.watcher --webcam
python -m apps.enroll --name Ruth    # face enrollment (local only)
python -m apps.score                 # replay scorer on labeled clips
python -m apps.redteam911            # 911 agent honesty red-team
python -m apps.build_routes          # precompute walking routes for receivers
python -m dashboard.server           # laptop map
python -m pytest
```

## Build order

1. Skeleton: state machine, interfaces + mocks, `simulate.py` (4 stories), tests.
2. Webcam/video pose (YOLO + ByteTrack), fall and stillness detectors, the field-of-view rule, and a
   replay scorer for labeled clips.
3. Face enrollment + lock, and button attribution.
4. Pan-tilt tracker, eyes, buzzer, button on the Pi 5.
5. Voice (Piper TTS + Vosk).
6. Map dashboard + receivers + routes + trigger.
7. Twilio notifier and the 911 agent (template first, then Claude Q&A + filter), with `--dry-run`.
8. Integration, eval numbers, filming.

No stretch goals. Judging is a 3-minute Devpost video: problem solving, innovation, technology, design, completion.
