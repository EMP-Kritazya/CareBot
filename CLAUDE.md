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
  quotes. Unknown means "I don't know." Sandy hears "someone is coming" only after a person says YES.
- **Privacy:** video and face embeddings never leave the robot. Only JSON events go over the network.
- **Field of view:** act only on what the camera sees. Out of view is not an emergency.
- **It is a robotics project.** The camera sits on a pan-tilt servo head that follows the resident.
- **Claude is in the middle, never in charge.** The state machine decides actions and what may be said;
  Claude (`carebot/ai/llm.py`, `claude-opus-5-5`, low effort) only phrases lines, understands speech and
  answers the 911 dispatcher from the fact sheet. Every Claude output passes a code check
  (`check_line`, `check_answer`) or the template/keyword fallback is used. Text only: never send camera
  frames or face data. Everything must work with no key or no connection.
- Integration (robot assembled): follow `docs/INTEGRATION.md` step by step.
- Never log or commit secrets. Credentials live in `.env` (git-ignored). Keep `.env.example` current.

## System

- **The robot:** Raspberry Pi 5 (4GB). There is **no AI Camera (IMX500)**; it is out of scope.
  - Camera: a Logitech USB webcam on a blue USB 3 port, read with OpenCV / V4L2 (MJPG, 1280x720,
    1-frame buffer; found by name: `camera.source: usb`; never mirrored on the robot). Video never
    touches a network. Laptop testing uses the laptop's webcam. (`picamera` still works, unused.)
  - Pose: YOLO11n-pose and ByteTrack on those frames (17 COCO keypoints, multi-person).
    The fallback is MediaPipe Pose, single person.
  - Pan-tilt: two Hitec D625MW servos on GPIO 12 (pan) and 13 (tilt), using Pi 5 hardware PWM
    (`dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4`, plus `rpi-hardware-pwm`). They run off their own 6 V
    battery (never the Pi's 5 V) with a common ground (`docs/wiring.md`). No PCA9685; pigpio doesn't run on the Pi 5.
  - LED eyes: a NeoPixel arc on SPI GPIO 10 (`adafruit-circuitpython-neopixel-spi`) through a level
    shifter. rpi_ws281x doesn't run on the Pi 5.
  - Buzzer on GPIO 17. The button is on GPIO 27.
  - Speech out: a JBL speaker over Bluetooth (fallback: AUX through a USB sound card). Speech in: an
    INMP441 I2S mic (fallback: the Logitech's built-in mic).
  - The robot runs the Mosquitto broker.
- **The laptop:** the map dashboard (FastAPI + WebSocket + Leaflet/OSM). It subscribes to the robot
  over MQTT, simulates the receivers that don't have real phones, and has a "Trigger alert" button.
- **No second unit.** The Pi 4 is a spare robot brain only.
- **Link:** MQTT over our own travel router. Topics: `carebot/robot-4A/event`,
  `carebot/robot-4A/cmd`, `carebot/robot-4A/receivers`.
- **Escalation, all fired at once on ALERTED:**
  - Neighbors who agreed to help (receivers in `config/receivers.yaml`), rung in waves of 3,
    nearest first by precomputed walking time (`data/routes.json`). A YES updates the family and 911.
  - The family, by a WhatsApp text (Twilio sandbox; plain SMS once A2P-registered). `twilio.family: call|both` adds a call.
  - The 911 agent: a templated briefing, then Q&A with Claude, answering only from the fact
    sheet (`carebot/escalate/facts.py`, `carebot/ai/dispatcher.py`). CareBot never hangs up on 911 (it
    holds the line); updates are spoken into the open call.

Both machines are headless or remote. We work over SSH (`ssh <user>@carebot.local`).
The live view is an MJPEG stream on port 8000 (`--headless`). It shows **skeletons on black** by
default so the room never appears on the network; `--view camera` only while aiming the camera.
Head follow pans, and tilts only after 1.5 s off-center vertically (longer than a fall's drop window,
so a fall is seen first; falls are measured vertically) and the
head freezes during a possible fall, a check-in or an alert (`carebot/vision/follow.py`).

## Architecture

Every hardware part sits behind a small interface with a mock, chosen in `config/*.yaml`
(`hardware: mock | pi`). Everything must run on a laptop with mocks.

| Module | Real | Mock |
| --- | --- | --- |
| pose source | webcam + YOLO11n-pose/ByteTrack (NCNN on Pi), MediaPipe fallback, video file | video file |
| face lock | OpenCV YuNet + SFace, bound to a ByteTrack ID | fixed resident track ID |
| tracker | Hitec D625MW servos (hardware PWM) | prints angles |
| eyes / buzzer / button | NeoPixel SPI, GPIO | red terminal banner; type `b` + Enter = button |
| voice | Piper/espeak-ng out, Vosk in (keywords; full transcript for "who's there"; 15 s timeout) | keyboard |
| notifier | Twilio SMS + calls | console |
| 911 agent | fact sheet + Claude Q&A + code filter, over a Twilio call | offline answers from facts |
| phrasing | Claude rewrites lines; `check_line` must pass or the template is spoken | template |
| understanding | Claude reads whole answers; keyword "help" / symptoms always win | keyword rules |
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
0.42, two checks in a row; a friend whose face doesn't match is never assumed to be him) binds to the track whose nose and eye keypoints lie inside the face box; that track becomes
`resident_track_id`. The lock stays with the ByteTrack ID while the face is hidden. If one person is
in view and nobody is locked, assume it's the resident (config flag).

The lock decides who the head follows, whose stillness counts, whose name is spoken, and who pressed
the button. A fall by anyone in view still triggers a check-in, without a name if it isn't Sandy.

### State machine

Pure, no I/O, fully unit tested.

- **MONITORING.** A fall seen and not recovered, resident stillness in view, or the map trigger goes
  to CHECK_IN.
- **CHECK_IN** (2 asks):
  - Voice "I'm OK", or the button followed by voice "I'm OK", goes back to MONITORING.
  - "Help", no answer after 2 asks, or the button followed by 2 asks with no voice goes to ALERTED.
- **ALERTED.** The robot beeps, and neighbors, family and 911 are contacted.
  - A neighbor's YES sets `on_the_way`.
  - The button pressed by someone else (Sandy's track still lying down or out of view) starts "Who
    are you, and what's happening?" The reply sets `with_resident`, `hospital`, `resident_ok` and so on,
    and is sent as a verbatim quote.
  - The button pressed by Sandy plus a spoken "I'm OK" goes to CLOSED.
- **CLOSED.** Back to MONITORING.

The button stops the beeping and pauses escalation. It never closes an alert by itself.

Getting up (`GotUp`, the camera saw the person who fell back on their feet) is never a cancel either.
During CHECK_IN the asks are reworded ("I see you got up..."), and if they then don't answer, only
the family is texted (no 911, no neighbors). During ALERTED it's passed on as a fact to the family and
911, and help is not called off. Only the fallen person's own spoken "I'm OK" closes an incident.

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
  - he stays down for `fall_hold_s` (10 s), OR
  - he leaves the frame and doesn't reappear upright within `fall_out_of_view_s` (10 s).

  Standing back up within the hold means no alert (`fall_recovered`). The servos freeze once a fall
  candidate starts.
- **Stillness:** only the resident, only while in view, never lying. Mean keypoint motion stays under the
  threshold for a timer that depends on what the camera sees: sitting calmly 20 min, standing 3 min,
  slumped 1 min (`--demo`: 10 s / 7 s / 5 s, plus a 5 s fall hold and 5 s answer windows). Every incident close emits `RestartStillness`: the
  timer restarts with a cooldown (10 min, demo 5 s), and after "I'm OK" to a stillness check the
  next one backs off (x2, max x4) until he's up and moving. A low head beside the shoulders is lying
  (sleep), never "slumped".
- **Out of view:** do nothing. No missing-person alert. The head returns home and waits.

## Conventions

- Python 3.11 (Raspberry Pi OS Bookworm). Lazy-import hardware and service libraries (`gpiozero`,
  `rpi_hardware_pwm`, `neopixel_spi`, `picamera2`, `ultralytics`, `mediapipe`, `paho`, `twilio`, `vosk`,
  `anthropic`) inside the classes that use them, so laptops never need them for the mocks.
- On the Pi, the venv is created with `--system-site-packages`: `gpiozero` and `lgpio` (button,
  buzzer) come from apt. The webcam goes through OpenCV (`v4l-utils` for checks).
- Add or update a pytest test with every detector or state-machine change. Run `python -m pytest` before committing.
- Small commits after each working step, on `main`. Tag `v-h12` at hour 12 and `v-demo` before filming.
- Prefer simple and robust over clever. This is a 24-hour demo.

## Commands

```bash
python -m apps.simulate              # every user story, scripted, no hardware
python -m apps.watcher --video samples/fall_01.mp4
python -m apps.watcher --webcam 1      # full loop on a laptop: real voice in/out, mocked hardware + alerts
python -m apps.watcher --webcam 1 --sim-neighbor 20 --demo   # short demo timers for the video
python -m apps.vision_live --webcam 1  # vision only (skeletons, face lock, detector events)
python -m apps.enroll --name Sandy    # face enrollment (local only)
python -m apps.score                 # replay scorer on labeled clips
python -m apps.redteam911            # 911 answers red-team (Claude if ANTHROPIC_API_KEY is set; --no-ai)
python -m apps.watcher --webcam 1 --no-ai   # templates + keywords only
python -m apps.watcher --config config/pi.yaml --headless   # the robot: Logitech webcam, no window, keys typed over SSH,
                                                          # live view at http://carebot.local:8000 (skeletons only)
python -m apps.watcher --webcam 1 --follow  # laptop: also run head follow (mock servo angles printed)
python -m apps.bench_pose [--camera usb] [--export]   # pose speed per model size (pick for the Pi)
python -m apps.watcher --webcam 1 --twilio-dry-run   # print every call CareBot would place
python -m apps.watcher --webcam 1 --twilio           # real calls (upgraded account + docs/TWILIO.md)
python -m apps.build_routes          # precompute walking routes for receivers (OSRM foot -> data/routes.json)
python -m dashboard.server --mqtt carebot.local   # response console at http://localhost:8050 (Live | Manual)
python -m apps.watcher --webcam 1 --map --demo    # laptop: watcher + map in one process (no broker)
python -m pytest
```

## Build order

1. Skeleton: state machine, interfaces + mocks, `simulate.py` (the user stories), tests.
2. Webcam/video pose (YOLO + ByteTrack), fall and stillness detectors, the field-of-view rule, and a
   replay scorer for labeled clips.
3. Face enrollment + lock, and button attribution.
4. Pan-tilt tracker, eyes, buzzer, button on the Pi 5.
5. Voice (Piper TTS + Vosk).
6. Map dashboard + receivers + routes + trigger.
7. Twilio notifier and the 911 phone line (Claude answers from the fact sheet), with `--dry-run`.
8. Integration, eval numbers, filming.

No stretch goals. Judging is a 3-minute Devpost video: problem solving, innovation, technology, design, completion.
