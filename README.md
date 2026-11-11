# CareBot

A small pan-tilt robot that watches over an older adult who lives alone. When they fall or stop
moving, it asks out loud if they're OK. If nobody answers, it contacts the nearest neighbors who
agreed to help, texts the family, and calls "911" with only what it actually saw and heard.

Built in 24 hours at **MakeCU** (Healthcare track).

> "Tom's phone was on the table, just out of reach. His neighbor had a key. For five days, nothing
> told anyone he had fallen."

## Why

In February 2023, Tom Notti, 92, fell in his living room in Cloverdale, California, and couldn't get
up. His phone was out of reach. He lay on the floor for about five days before a Meals on Wheels
driver saw him through a window. His next-door neighbor had a key the whole time.

- More than 14 million adults aged 65+ (about 1 in 4) report a fall each year (CDC).
- Of fallers over 90, 80% couldn't get up after at least one fall, and 30% lay on the floor for an
  hour or more. Call alarms were available but mostly unused in those long lies (Fleming & Brayne,
  BMJ 2008).

Today's tools all need something from the person who fell. A pendant has to be worn and pressed, a
pull cord can't be reached from the floor, and a smartwatch has to be charged. Cloud cameras feel
like surveillance. None of them reaches the person two doors away, and none keeps 911 accurately
informed as things change.

## What it does

1. **Watches.** A webcam on a servo pan-tilt head follows the resident (we call him Sandy), using
   his face to tell him apart from visitors. It tracks 17 body keypoints for each person in view.
2. **Notices.** A fall: the hips drop fast while the torso tips over, and he doesn't get back up.
   Or he goes still in view: slumped for 1 min, standing for 3 min, sitting calmly for 20 min.
3. **Checks in.** The eyes turn amber: "Sandy, I saw you fall. Are you OK? Say 'I'm OK' or 'help'."
   It asks twice.
4. **Escalates**, all at once, if there's silence or a "help":
   - The robot beeps and its eyes turn red.
   - The 3 nearest neighbors, ranked by walking time, get a phone call ("press 1 if you can go"),
     then the next 3 if nobody says yes.
   - The family gets a WhatsApp or SMS message (optionally a call as well).
   - The 911 stand-in is called with a briefing and can ask questions out loud. CareBot holds that
     line open and speaks each update into it.
5. **Keeps everyone updated.** When a neighbor presses 1 (YES), their walking route lights up on the map,
   and only then does Sandy hear "Krit is on his way." When someone presses the robot's button
   while Sandy is still on the floor, CareBot asks "Who are you, and what's happening?" Their reply
   goes word for word to the family and 911.

A laptop dashboard shows the whole incident on a real street map: Sandy's home, his neighbors and
what each has answered, the YES route, the timeline, and a **Trigger alert** button.

## Design rules

- **It never claims more than it knows.** The 911 agent answers only from a fact sheet built by code
  (what the camera saw, what was heard, who was contacted) and from verbatim quotes. "Is he
  breathing?" gets "I don't know. The device only knows what its camera sees and what it hears."
- **Code decides, Claude only phrases.** A pure, unit-tested state machine decides every action and
  what may be said. Claude rewrites lines so they sound natural, reads free-form answers, and
  answers the dispatcher from the fact sheet. Every Claude output passes a code check (`check_line`,
  `check_answer`); if it fails, the template or keyword fallback is used. CareBot works fully
  offline or without an API key.
- **Silence is never OK.** No answer escalates. Pressing the button stops the beeping but never
  closes an alert by itself, and neither does getting up. Only the fallen person's spoken "I'm OK"
  closes an incident.
- **Privacy.** Video and face embeddings never leave the robot. Only JSON events go over the
  network. The live view shows skeletons on black by default, and nothing visual is sent to Claude.
- **Field of view.** CareBot acts only on what it sees. Sandy leaving the room is not an emergency,
  and a fall in another room is not seen. We say so.
- **No medical guidance.** It alerts people. It never diagnoses or interprets anything.
- **The 911 call is a stand-in.** It rings a teammate's phone. It never dials real 911.

## Hardware

| Part     | What we used                                                                                     |
| -------- | ------------------------------------------------------------------------------------------------ |
| Brain    | Raspberry Pi 5 (4 GB)                                                                            |
| Camera   | Logitech USB webcam (OpenCV / V4L2, MJPG 1280×720), also the microphone                         |
| Head     | Two Hitec D625MW servos, pan on GPIO 12 and tilt on GPIO 13, Pi 5 hardware PWM, own 6 V battery |
| Eyes     | NeoPixel arc on SPI (GPIO 10) through a 74AHCT125 level shifter                                  |
| Buzzer   | GPIO 17                                                                                          |
| Button   | GPIO 27                                                                                          |
| Speaker  | JBL over Bluetooth (fallback: USB sound card)                                                    |
| Network  | Our own travel router, Mosquitto MQTT broker on the Pi                                           |

Eye colors: blue means watching, amber checking in, red alerted, and green means someone is on
the way. Wiring is in [docs/wiring.md](docs/wiring.md).

## How it works

```
 Robot (Pi 5)                                                         Laptop
 ┌───────────────────────────────────────────────────────────┐       ┌──────────────────────┐
 │ webcam ─► YOLO11n-pose + ByteTrack (NCNN) ─► face lock    │       │ dashboard (FastAPI,  │
 │              │                              (YuNet+SFace) │ MQTT  │ WebSocket, Leaflet)  │
 │              ▼                                             │◄─────►│ simulated neighbors  │
 │   fall / stillness detectors ─► state machine ─► actions   │ JSON  │ Trigger alert        │
 │              ▲                     │    ▲                  │ only  └──────────────────────┘
 │   pan-tilt follow         voice in/out  Claude (text only, │
 │                           eyes, buzzer,  checked by code)  │──► Twilio: neighbors, family, 911
 │                           button                           │
 └───────────────────────────────────────────────────────────┘
```

Every hardware part sits behind a small interface in [carebot/hw/base.py](carebot/hw/base.py) with a
mock, chosen by `hardware: mock | pi` in `config/*.yaml`. The whole system runs on a laptop with
mocks: the laptop webcam, typed or spoken answers, and console alerts.

| Area          | Code                                                       | Notes                                                                                        |
| ------------- | ---------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| Pose          | [carebot/vision/pose.py](carebot/vision/pose.py)           | YOLO11n-pose with ByteTrack, multi-person, NCNN on the Pi (capped at 5 fps for heat)         |
| Face lock     | [carebot/vision/identity.py](carebot/vision/identity.py)   | YuNet + SFace; a match (cosine ≥ 0.42, twice in a row) binds to a ByteTrack ID               |
| Detectors     | [carebot/vision/detectors.py](carebot/vision/detectors.py) | Fall and stillness, measured in body sizes; thresholds in `config/detectors.yaml`            |
| Head follow   | [carebot/vision/follow.py](carebot/vision/follow.py)       | Pans to the resident; freezes during a possible fall, a check-in or an alert                 |
| State machine | [carebot/core/state.py](carebot/core/state.py)             | MONITORING → CHECK_IN → ALERTED → CLOSED; pure, no I/O                                       |
| Voice         | [carebot/voice/](carebot/voice/)                           | espeak-ng on the Pi (macOS `say` on a laptop); Vosk offline recognition                      |
| Claude        | [carebot/ai/](carebot/ai/)                                 | Phrasing, understanding, and 911 Q&A, all behind code checks                                 |
| Escalation    | [carebot/escalate/](carebot/escalate/)                     | Fact sheet, neighbor ranking and call waves, Twilio texts and calls                          |
| Link          | [carebot/net/](carebot/net/)                               | MQTT topics `carebot/robot-4A/{event,cmd,receivers}`; in-memory when the map runs in-process |
| Map           | [dashboard/](dashboard/)                                   | FastAPI + WebSocket server, Leaflet page                                                     |

The full design is in [PLAN.md](PLAN.md): user stories, what CareBot may say in each state, the
detector rules and the demo.

## Quick start (laptop)

Python 3.11 or newer.

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # optional: Twilio and Claude keys. Everything runs without them.
python -m pytest                  # 331 passed, 2 skipped (Pi-only)
```

Models go in `models/`, which is git-ignored:

- `yolo11n-pose.pt`, downloaded by Ultralytics on first run. NCNN exports are made with
  `python -m apps.bench_pose --export`.
- `face_detection_yunet_2023mar.onnx` and `face_recognition_sface_2021dec.onnx`, from OpenCV Zoo.
- `vosk-model-small-en-us-0.15`, from alphacephei.com/vosk/models.

Then:

```bash
python -m apps.simulate                          # every user story, scripted, no hardware
python -m apps.enroll --webcam 1                 # save the resident's face (local only, with consent)
python -m apps.watcher --webcam 1 --map --demo   # full loop + map at http://localhost:8050, short timers
```

`--demo` shortens the timers for filming: stillness after 10 / 7 / 5 s, a 5 s fall hold and 5 s
answer windows. In the watcher's terminal, `b` + Enter presses the button, and typed answers always
work.

## Commands

| Command                                                      | What it does                                                       |
| ------------------------------------------------------------ | ------------------------------------------------------------------ |
| `python -m apps.simulate [--story N]`                        | Plays the user stories through the real state machine, narrated    |
| `python -m apps.vision_live --webcam 1`                      | Vision only: skeletons, face lock, detector events                 |
| `python -m apps.watcher --webcam 1`                          | The whole loop on a laptop with mocked hardware and alerts         |
| `python -m apps.watcher --webcam 1 --sim-neighbor 20`        | A simulated neighbor says YES 20 s after the alert                 |
| `python -m apps.watcher --webcam 1 --no-ai`                  | Templates and keywords only, no Claude                             |
| `python -m apps.watcher --webcam 1 --twilio-dry-run`         | Prints every call and text CareBot would send                      |
| `python -m apps.watcher --webcam 1 --twilio`                 | Real calls and texts (see [docs/TWILIO.md](docs/TWILIO.md))        |
| `python -m apps.watcher --config config/pi.yaml --headless`  | On the robot: live skeleton view at `http://carebot.local:8000`    |
| `python -m dashboard.server --mqtt carebot.local`            | The map, subscribed to the robot's broker                          |
| `python -m apps.remote_mic --mqtt carebot.local`             | The laptop mic as the robot's ears (sends text only, while listening) |
| `python -m apps.redteam911 [--no-ai]`                        | Hard dispatcher questions across incident states, graded by code   |
| `python -m apps.call_test {neighbor,family,911}`             | One real phone call for one role                                   |
| `python -m apps.live_incident --public-url <tunnel>`         | A simulated incident with real phones, end to end                  |
| `python -m apps.build_routes [--offline]`                    | Precomputes walking routes to `data/routes.json`                   |
| `python -m apps.bench_pose [--camera usb] [--export]`        | Pose speed per model size, to pick one for the Pi                  |
| `python -m apps.hwcheck --all`                               | Bench check of every hardware part (on the Pi)                     |

## On the robot

Follow [docs/INTEGRATION.md](docs/INTEGRATION.md) step by step: OS and network, packages, camera,
pose speed, drivers, speaker and mic, face, head follow, the full demo run, and start on boot. The
hardware driver contract is in [docs/HARDWARE.md](docs/HARDWARE.md). In short:

```bash
python3 -m venv --system-site-packages .venv     # gpiozero and lgpio come from apt
.venv/bin/pip install -r requirements.txt -r requirements-pi.txt
.venv/bin/python -m apps.hwcheck --all
.venv/bin/python -m apps.watcher --config config/pi.yaml --headless
```

## Configuration

- `config/mock.yaml` (laptop) and `config/pi.yaml` (robot): the resident's profile, check-in timing,
  voice, Claude, camera, head follow, pose model, Twilio and transport.
- `config/detectors.yaml`: every fall and stillness threshold. Nothing is hard-coded.
- `config/hardware_pi.yaml`: pins, servo limits and LED count.
- `config/receivers.yaml`: the neighbors who agreed to help. They're fictional names on real blocks,
  and each is either `call` (a real phone, numbers read from `.env`) or `sim` (simulated by the
  dashboard).
- `.env`: Twilio credentials, the family, neighbor and 911 stand-in phone numbers, the tunnel URL,
  and `ANTHROPIC_API_KEY`. It is git-ignored, so copy it from `.env.example`.

## Limits

- One robot watches one room. Falls out of view are not detected.
- The webcam needs light, so the room needs a night-light.
- Someone who says "I'm OK" when they aren't is believed.
- With no internet, the robot still beeps and the map still shows the alert over the local network,
  but texts and calls fail.
- The 911 call is a demo stand-in. A real product would use an NG911 data integration.

## Credits

All code in this repository was written during the hackathon.

- [Ultralytics YOLO11](https://github.com/ultralytics/ultralytics) pose model (AGPL-3.0) and its
  built-in [ByteTrack](https://github.com/ifzhang/ByteTrack) tracker
- [Tencent NCNN](https://github.com/Tencent/ncnn), for fast inference on the Pi
- [OpenCV](https://opencv.org/) and [OpenCV Zoo](https://github.com/opencv/opencv_zoo): YuNet face
  detection and SFace face recognition
- [Vosk](https://alphacephei.com/vosk/) offline speech recognition, small English model;
  [sounddevice](https://python-sounddevice.readthedocs.io/)
- [espeak-ng](https://github.com/espeak-ng/espeak-ng) and macOS `say` for speech
- [Anthropic Claude](https://www.anthropic.com/) via the `anthropic` Python SDK
- [Twilio](https://www.twilio.com/) for SMS, WhatsApp (sandbox) and voice calls, reached through a
  [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) (`cloudflared`)
- [Eclipse Mosquitto](https://mosquitto.org/) and [paho-mqtt](https://github.com/eclipse/paho.mqtt.python)
- [FastAPI](https://fastapi.tiangolo.com/) and [uvicorn](https://www.uvicorn.org/)
- [Leaflet](https://leafletjs.com/) 1.9.4, map data © [OpenStreetMap](https://www.openstreetmap.org/copyright)
  contributors, tiles by [CARTO](https://carto.com/attributions)
- [OSRM](http://project-osrm.org/) foot routing on the [FOSSGIS](https://www.fossgis.de/) server
- [gpiozero](https://gpiozero.readthedocs.io/), [lgpio](https://abyz.me.uk/lg/py_lgpio.html),
  [rpi-hardware-pwm](https://github.com/Pioreactor/rpi_hardware_pwm),
  [Adafruit Blinka and CircuitPython NeoPixel SPI](https://github.com/adafruit/Adafruit_CircuitPython_NeoPixel_SPI)
- [PyYAML](https://pyyaml.org/) and [pytest](https://pytest.org/)
- Google Fonts: Space Grotesk, Inter, JetBrains Mono

## Sources

- Mary Callahan, The Press Democrat, Feb 23, 2023:
  ["It's just sad because the whole thing was preventable": Cloverdale man, 92, lay for days after fall before being found](https://www.pressdemocrat.com/2023/02/23/its-just-sad-because-the-whole-thing-was-preventable-cloverdale-man-92-lay-for-days-after-fall-before-being-found/)
- Fleming J, Brayne C. [Inability to get up after falling, subsequent time on floor, and summoning help](https://www.bmj.com/content/337/bmj.a2227). BMJ 2008;337:a2227
- CDC: [Older Adult Falls Data](https://www.cdc.gov/falls/data-research/index.html)
- ACP Internist: [Falls leading to more deaths in adults ages 65 years and older](https://immattersacp.org/weekly/archives/2025/06/24/4.htm)
