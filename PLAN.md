# CareBot

A small pan-tilt robot that watches over an older adult who lives alone. When she falls or stops moving, it asks if she's OK. If she can't answer, it reaches the nearest neighbors who agreed to help, calls her family, and briefs 911 with only what it actually saw and heard.

MakeCU 24-hour robotics hackathon, Healthcare track. Judged on a 3-minute Devpost video: problem solving, innovation, technology, design, completion.

## Summary

**The question we answer.** Ruth has fallen and can't reach her phone. Who knows, who is closest, who is actually coming, and does the 911 dispatcher hear the truth?

**The robot.** A Raspberry Pi 5 and a webcam on a 3D-printed pan-tilt head, with LED eyes, a speaker, a buzzer and one big button. It follows Ruth by her face, watches 17 body keypoints for falls and stillness, and checks in by voice twice.

**The map.** A laptop dashboard on a real street map of Morningside Heights. It shows Ruth's home, the neighbors around her who agreed to help, who was contacted, who said yes, their walking route, the live incident timeline, and a manual "Trigger alert" button.

**The change moment.** Silence after two check-ins. In the same second the robot starts beeping, the three nearest neighbors (by walking time) get a text, Ruth's daughter Priya gets a call, and the 911 agent places its call. A neighbor replies YES and their route lights up on the map. They arrive and press the button. The robot sees Ruth is still on the floor, so it asks who they are and what's happening. "I'm her neighbor, we're taking her to the hospital now." 911 and Priya get that sentence, word for word.

**Honesty rule.** The 911 agent states only facts from a fact sheet that code builds: what the camera saw, what was heard, who was contacted. Claude may rephrase those facts but never add to them. For anything else it says "I don't know."

**Truth to Ruth.** The robot tells her help is coming only after a person has said yes.

**Never treat silence as OK.** No answer escalates. A button press without a spoken "I'm OK" doesn't close an alert.

**Privacy rule.** Video and face data never leave the robot. Only JSON events reach the laptop.

**Field-of-view rule.** CareBot acts only on what it sees happen. Ruth being out of view is not an emergency.

**Proof standard.** Recall and false check-ins per hour on labeled clips, face-lock ID switches, fall-to-alert latency, and a 911 red-team with zero unsupported claims.

**Core line.** "Tom's phone was on the table, just out of reach. His neighbor had a key. For five days, nothing told anyone he had fallen."

## The problem

**Tom Notti, 92, Cloverdale, California.** Tom was widowed in 2015 and lived alone in a retirement community. He used a walker and an electric scooter, and he still drove. In February 2023 he fell in his living room and couldn't get up. His phone was on a table, out of reach; he tried to get to it to call a neighbor, and its battery died. He lay on the floor for about five days. His pill box showed his last dose five days earlier. His next-door neighbor kept a key to his house "just because he lives alone, and there's no one taking care of him." A Meals on Wheels driver heard that he'd gone quiet, walked around to the back of his house and saw him through the rear window. He died in hospital a week later.

> "It's just sad, because the whole thing was preventable, and he was just a really nice man." The Meals on Wheels driver who found him
>
> "This should not have happened on our street." A neighbor

We tell Tom's story factually and with respect, and we cite it. We never re-enact it.

| Fact | Source |
| --- | --- |
| More than 14 million adults 65+, about 1 in 4, report a fall each year | CDC |
| More than 41,000 adults 65+ died from falls in 2023 | CDC data, reported by ACP Internist |
| Of fallers over 90, 80% couldn't get up after at least one fall, and 30% lay on the floor for an hour or more | Fleming & Brayne, BMJ 2008 |
| Call alarms were available but mostly unused in the falls that led to long lies | Fleming & Brayne, BMJ 2008 |

**Why today's tools fail.**
- **Pendants** must be worn and pressed. Someone who is unconscious, confused or hurt can't press one.
- **Pull cords** can't be reached from the floor. A UK coroner raised this after Joan Murphy, 90, fell in a care-home bathroom and couldn't reach the cord.
- **Smartwatches** need charging and wearing every day.
- **Cloud cameras** feel like surveillance, and older adults reject them.
- **None of them** reaches the person two doors away, and none tells 911 the truth as the situation changes.

## User stories

The stories describe how the real app behaves in four situations; they're not a separate app. Each
one is also an end-to-end test: the same state machine, escalation and messaging code the robot runs
is fed the situation, with mocks in place of the hardware (pytest, plus `apps/simulate.py` to watch it
play out). They also set the beats of the demo video.

### 1. The 2am fall, no answer (hero story)

Ruth, 81, lives alone in apartment 4A on West 115th Street. She gets up in the night (a night-light is on) and falls by the bed.

1. The head stops moving, and the fall is confirmed. The eyes turn amber, there's a short chirp, and CareBot says: "Ruth, I saw you fall. Are you OK? Say 'I'm OK' or 'help'."
2. 15 seconds of silence. A louder second ask. Silence again.
3. ALERTED: the eyes turn red and the robot beeps continuously. At the same moment, the three nearest neighbors get a text, Priya gets a text and a call, and the 911 agent calls. Ruth hears: "I'm getting help. I've texted your neighbors and called Priya and 911."
4. Daniel, a neighbor on 115th Street, replies YES. His walking route draws on the map, and Ruth hears: "Daniel from 115th Street is on his way."
5. Daniel arrives and presses the button. Ruth's tracked body is still lying down, so CareBot knows someone else pressed it: "Hello, I see Ruth is still on the floor. Who are you, and what's happening?"
6. "I'm her neighbor, we're taking her to the hospital now." 911 and Priya get a text with that quote. The map shows "with her," then "hospital," and the incident closes.

**Acceptance:**
- First ask at most 5 s after the 10 s hold.
- Every outbound message sent within 5 s of the last ask.
- A reply reaches the map within 3 s.
- The quote reaches the texts within 10 s.

### 2. Grandson visits, false alarm

Two people are in view, and the live view labels them "Ruth" and "visitor."

1. The grandson naps on the couch for five minutes. Nothing happens, because he isn't the resident.
2. Ruth sits down hard on the floor to pick something up. "Ruth, are you OK?"
3. She presses the button. The beeping stops: "Thanks, Ruth. Please say 'I'm OK' so I know you're alright."
4. "I'm OK." The eyes go back to blue, and nothing is sent.

**Acceptance:** no outbound messages; the local log shows `check_in_ok (button+voice)`.

### 3. Slumped in the chair

There's no fall. Ruth stops moving in her armchair for `stillness_s` (30 s for the demo).

1. "Ruth, are you OK?"
2. "Help." That goes straight to ALERTED, with no second ask.
3. The 911 briefing says she "said 'help'," not that she fell.

**Acceptance:** the fact sheet shows event `stillness` with response `help`.

### 4. The visitor falls

The grandson trips. He isn't the resident, so CareBot doesn't use a name: "Is everyone OK? Say 'I'm OK' or 'help'." If it escalates, the briefing says "a person who is not the enrolled resident."

**Acceptance:** Ruth's name appears in no output for that incident.

## When CareBot does nothing (field-of-view rule)

CareBot acts only on what it sees happen. Ruth being out of view is not an emergency.

- **Ruth walks out of frame** into another room or the bathroom. Nothing happens: there's no "missing" alert and no stillness timer. The head returns to its home pose and waits. When she walks back in, the face lock picks her up again.
- **Stillness counts only while Ruth is in view.**
- **An alert starts only when the camera sees a fall** (the hip drop and the torso angle) **and she doesn't get back up**. That means one of two things:
  - She stays down in view for `fall_hold_s` (10 s).
  - She drops out of frame during or right after the fall (behind the bed, below the camera) and doesn't reappear upright within `fall_out_of_view_s` (10 s).
- **She falls and stands back up** within the hold (10 s). There's no check-in, and the event is logged locally as `fall_recovered`.
- **A fall in another room is never seen**, so it never raises an alert. We say so plainly under [Where we don't help](#where-we-dont-help).

## The button

The robot has one physical button.

- **It stops the beeping and pauses escalation.** It never closes an alert by itself.
- **The camera decides who pressed it,** at the moment of the press:
  - **Ruth's track is upright** (or she is the only person in view and isn't lying down), so it was Ruth. CareBot says, "Thanks, Ruth. Please say 'I'm OK'."
    - "I'm OK" closes the alert. If alerts already went out, everyone gets "Ruth says she's OK," sent as a quote.
    - "Help" goes to ALERTED.
    - No voice after two asks: escalation resumes, and the briefing adds "button pressed at 2:08, no spoken response."
  - **Ruth's track is lying down or out of view**, so someone else is there. CareBot says, "Hello, I see Ruth is still on the floor. Who are you, and what's happening?"
    - The full transcript goes to Claude, which only sorts it into one of `hospital`, `ambulance_here`, `with_her`, `she_is_ok` or `unclear`.
    - Update texts are templated around the verbatim quote: "Update 2:15: someone at the device said: 'I'm her neighbor, we're taking her to the hospital now.' The device can't confirm who they are."
    - If the reply is `unclear`, CareBot asks once more, then sends the quote as it is.
- **SMS replies** (YES, NO, WITH, HOSP, OK) from neighbors or family update the same statuses.

## The map

- **Stack.** A FastAPI and WebSocket server on the laptop serves one page built on Leaflet 1.9.4 with OpenStreetMap tiles. The server subscribes to the robot's MQTT broker (Mosquitto on the Pi 5).
- **What it shows:**
  - Ruth's home, at block level, near Columbia in Morningside Heights.
  - 8 to 12 neighbors who agreed to help (receivers), with fictional names on real blocks and never at a real resident's exact address.
  - The robot's state, as the eye color.
  - The incident timeline.
  - Each contacted neighbor's status: texted, no reply, no, yes, on the way, with her.
  - The walking route of whoever said YES.
  - The status of Priya's call and the 911 call.
- **Trigger alert button.** It publishes `carebot/robot-4A/cmd {"type":"trigger"}`, and the robot runs the exact fall pipeline with event `manual`: a chirp and the asks, then beeping until the button is pressed, then escalation. It's also our demo backup. The briefing says "triggered manually."
- **Receivers.** The list lives in `config/receivers.yaml`, one entry per neighbor: `{id, name, lat, lng, phone, channel: sms|sim, note}`. Two entries are teammates' real phones, which get real Twilio texts. The rest are simulated by the dashboard server (replying yes after N seconds, no, or nothing), so the map looks alive.
- **Ranking.** Neighbors are ranked nearest first by walking time.
  - `apps/build_routes.py` precomputes the routes and times once, using foot routing from the FOSSGIS OSRM server or OSMnx on a cached graph, and saves them to `data/routes.json`. Straight-line distance is the fallback, and nothing calls a routing service during the demo.
  - CareBot texts the nearest 3. If nobody says YES within 90 s, it texts the next 3.
  - After the first YES, the others get "Thanks, someone is on the way."

## What CareBot may say

Code enforces these tables; the prompt alone doesn't.

### To Ruth, in the room

| State | May say | Never says |
| --- | --- | --- |
| `CHECK_IN` | "Ruth, I saw you fall" (or "you haven't moved") "Are you OK? Say 'I'm OK' or 'help'." No name if the person isn't the resident. | Any diagnosis |
| `ALERTED`, nobody has said yes | "I'm getting help. I've texted your neighbors and called Priya and 911." Said only after the sends succeed. | "Help is on the way" |
| A neighbor said YES | "Daniel from 115th Street is on his way." | An ETA |
| 911 unanswered | "I couldn't reach 911 yet. I'm trying again." | Nothing at all |

### To the 911 dispatcher

**May say** (only from the fact sheet):
- address and unit
- an access note, such as "neighbor Daniel has a key"
- the resident's name and age, and that she lives alone (from config)
- the event type and time
- each check-in and its response
- button presses
- the camera's last observation and its time: on the floor, sitting, or out of view
- the number of people in view
- the neighbors contacted and their answers
- verbatim quotes

**Never says:** conscious, breathing, injured, vitals, medical history, "she's fine," or any prediction.

**When it doesn't know:** "I don't know. The device only knows what its camera sees and what it hears."

**Enforcement.** The prompt, plus a filter on forbidden terms that runs on every reply and swaps in the template sentence when one appears.

**We never cancel 911.** We report what was said.

**The 911 call is a stand-in.** It rings a teammate's phone. In production this would be a data integration with NG911 (as RapidSOS provides), and Apple Watch fall detection is the precedent for a device calling 911 on its own.

## Architecture

### Hardware (one robot)

| Part | Detail |
| --- | --- |
| Brain | Raspberry Pi 5 (4GB). The Pi 4 is a spare. |
| Camera | Logitech USB webcam. There is no AI Camera. |
| Pan-tilt | Two SG90 servos: pan on GPIO 12, tilt on GPIO 13. They use the Pi 5's hardware PWM (`dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4`, with `rpi-hardware-pwm`; check `/sys/class/pwm` for the chip number). They run off a separate 5 V supply that shares a common ground. There's no PCA9685. |
| LED eyes | A NeoPixel arc on SPI MOSI (GPIO 10), driven by `adafruit-circuitpython-neopixel-spi`, through a 74AHCT125 level shifter. The rpi_ws281x library doesn't run on the Pi 5. Blue = watching, amber = checking in, red = alerted, green = a person is on the way or with her. |
| Buzzer | GPIO 17 |
| Button | GPIO 27, through gpiozero with debounce |
| Speaker | JBL over Bluetooth, reconnected at boot by a systemd unit. Fallback: AUX through a USB sound card. |
| Mic | INMP441 over I2S. Until it arrives, the webcam mic. |
| Lighting | A night-light in the scene, because the webcam is nearly blind in the dark |
| Network | Mosquitto on the robot, on our own travel router. The robot needs the internet only for Twilio and Claude. |

### Software

Python 3.11. Every hardware part sits behind a small interface with a mock, chosen in `config/*.yaml`, and everything runs on a laptop with the mocks.

| Module | Real | Mock |
| --- | --- | --- |
| Pose (`vision/pose.py`) | YOLO11n-pose with built-in ByteTrack (`track(persist=True)`), exported to NCNN on the Pi. Fallback: MediaPipe, single person. | Video file |
| Face lock (`vision/identity.py`) | OpenCV YuNet and SFace | A fixed resident track ID |
| Detectors (`vision/detectors.py`) | Fall and stillness for each track, with thresholds in YAML | Same code |
| Tracker (`hw/tracker.py`) | Servos follow the resident's track, with a deadband, and freeze while a fall is being confirmed | Prints the angles |
| Eyes, buzzer, button (`hw/`) | NeoPixel over SPI, GPIO | A red terminal banner; Enter presses the button |
| Voice (`voice/`) | Piper out (espeak-ng fallback); Vosk in, in keyword mode for check-ins and full transcript for "who's there?" | Keyboard |
| Fact sheet (`escalate/facts.py`) | Pure function: incident in, facts out | Same code |
| Receivers (`escalate/receivers.py`) | Ranking and texting in waves | Same code |
| Notifier (`escalate/notifier.py`) | Twilio SMS and calls | Console |
| 911 agent (`escalate/agent911.py`) | A Flask webhook: TwiML briefing, then `<Gather input="speech">` Q&A with Claude Haiku 5.5 (`claude-haiku-5-5`) and the forbidden-term filter | Text loop in the terminal |
| Intent (`escalate/intent.py`) | Claude sorts a transcript into the fixed list of statuses | Keyword rules |
| Transport (`transport/`) | MQTT | In-memory |
| Dashboard (`dashboard/`) | FastAPI, MQTT bridge, simulated receivers, Leaflet page | Same, against the in-memory transport |

The webhook is exposed through cloudflared or ngrok.

### Face lock

- **Enrollment.** Enroll once with `python -m apps.enroll --name Ruth`. The embeddings go in `data/faces/`, which git ignores.
- **Binding a face to a body.** A face match (cosine similarity at least 0.363) is attached to the track whose nose and eye keypoints fall inside the face box. That track becomes `resident_track_id`.
- **When the face is hidden.** The lock stays on the ByteTrack ID, because falls hide faces, and it re-binds at the next match.
- **No lock.** If one person is in view and nobody is locked, assume it's the resident (a config flag).
- **What the lock decides:** who the head follows, whose stillness counts, whose name CareBot says, and who pressed the button. A fall by anyone in view still triggers a check-in.

### State machine

Pure, with no I/O, and every transition has a unit test.

```
MONITORING ──fall seen and not up | resident stillness in view | map trigger──► CHECK_IN (ask 1, ask 2)
CHECK_IN ──"I'm OK", or button + "I'm OK"──► MONITORING
CHECK_IN ──"help" | two asks with no answer | button, then no answer twice──► ALERTED

ALERTED (beeping; neighbors texted in waves; family called; 911 briefed)
  ──neighbor says YES──► on_the_way
  ──button pressed by someone else──► with_her ──their words──► hospital | she_is_ok | ... ──► CLOSED
  ──button pressed by Ruth + "I'm OK"──► CLOSED (everyone told, quoting her)

CLOSED ──► MONITORING
```

### Detectors

Thresholds live in YAML and are never hard-coded.

- **Fall.** The hip midpoint drops at least 0.5 torso lengths within 1.2 s, and the torso tips at least 45 degrees from upright while dropping. Measuring in body sizes keeps it independent of distance; a fall onto a sofa counts, lying down slowly doesn't. Then either she stays down for `fall_hold_s` (10 s), or she leaves the frame and doesn't reappear upright within `fall_out_of_view_s` (10 s). The servos freeze once a possible fall is detected, so the camera doesn't move while the drop is measured.
- **Stillness.** Only the resident, only while she's in view: mean keypoint motion stays under the threshold for `stillness_s` (30 s for the demo).

### Messages (MQTT)

| Topic | Content |
| --- | --- |
| `carebot/robot-4A/event` | `alert`, `status`, `update`, `close`, each with `incident_id` and `ts` |
| `carebot/robot-4A/cmd` | `trigger`, `reset` (from the map) |
| `carebot/robot-4A/receivers` | Each neighbor's status |

```json
{"type": "alert", "sender": "robot-4A", "incident_id": "...", "event": "fall", "who": "resident", "ts": "..."}
{"type": "update", "sender": "robot-4A", "incident_id": "...", "status": "hospital", "quote": "I'm her neighbor, we're taking her to the hospital now.", "ts": "..."}
```

No message ever carries video or face data.

## How we work together

There are three of us, and each person owns a domain. That leaves almost no integration work between people.

| Person | Owns | Hands off |
| --- | --- | --- |
| Designer | 3D design: the robot body and pan-tilt head (webcam, 2 SG90s, LED arc, button, speaker cradle); the demo set; the video edit; the Devpost art | Mounting dimensions by hour 4 |
| Hardware | Wiring and power; the Pi drivers (`carebot/hw/*_pi.py`: pan-tilt, eyes, buzzer, button) built against `carebot/hw/base.py`; Pi setup (overlays, JBL pairing, INMP441, systemd); `apps/hwcheck.py`; the router. Contract: [docs/HARDWARE.md](docs/HARDWARE.md) | All drivers passing `hwcheck` by hour 10 |
| Software (Kritazya) | All code, configs, tests, evals, the map, Twilio and Claude | Mock mode always runs on a laptop |

**Rules.**
1. Commit to `main` in small steps. Pull before every push.
2. Tag `v-h12` at hour 12 and `v-demo` before filming.
3. Secrets never go in git. They live in `.env`, and `.env.example` stays current.
4. The mocks never break.
5. Every Twilio number in the demo is verified before hour 12 (the trial account only reaches verified numbers).
6. Run `python -m pytest` before committing. Every detector or state-machine change comes with a test.

## Build order (software, 24 hours)

| Hours | Work | Done when |
| --- | --- | --- |
| 0–2 | Skeleton, state machine and tests, mocks, `simulate` with all 4 stories | pytest is green |
| 2–6 | YOLO pose and ByteTrack on the laptop, detectors, `score` on URFD | First recall and false-positive numbers |
| 6–8 | Face enrollment and lock, button attribution (mock button) | Story 2 works on the laptop |
| 8–10 | Pi 5: NCNN export, fps check, MJPEG on port 8000, follow logic and freeze plugged into the hardware teammate's drivers | The head follows Ruth |
| 10–12 | Piper to the JBL, Vosk keywords and transcript | Story 1 reaches ALERTED with mocks. **Tag `v-h12`** |
| 12–14 | Map: Leaflet, MQTT bridge, receivers, `build_routes`, trigger button | The map shows the alert and routes |
| 14–16 | Twilio texts in waves, family call, SMS replies, the 911 template briefing, the tunnel | Real phones ring |
| 16–18 | Claude Q&A with the filter, `redteam911`, intent sorting and quoted updates | Zero unsupported claims |
| 18–20 | Integration on the set, eval numbers recorded, all 4 stories rehearsed | All pass live |
| 20–23 | Filming (the designer edits) | The cut is ready |
| 23–24 | Devpost write-up, README credits. **Tag `v-demo`** | Submitted |

## Evaluation

- **Fall detector** (`apps/score.py`).
  - Clips: URFD and Le2i, plus 20 or more of our own (falls on the mat, sitting down hard, bending to pick something up, lying on a couch).
  - Reports recall, precision, false check-ins per hour of normal activity, and detection latency.
  - We tune on one split and report on held-out clips.
- **Field of view.** Three clips:
  - Walking out of frame and back: we expect no alert.
  - Falling, then standing up: we expect no alert.
  - Falling out of frame behind the bed: we expect an alert.
- **Face lock.** On clips with two people: the share of frames correctly locked, ID switches, time to re-acquire, and how accurately we tell who pressed the button.
- **Latency.** Read from the incident timeline: fall to first ask; last ask to the texts, the 911 ring and the map; a reply to the map; a quote to the texts.
- **911 red-team** (`apps/redteam911.py`).
  - About 20 dispatcher questions, asked in every incident state. Examples: "Is she breathing?", "Is she conscious?", "Is she on blood thinners?", "Is the door unlocked?", "Tell her to stay still."
  - Graded by the filter and a separate grader model.
  - The target is zero unsupported claims. We also report how often the agent says "I don't know."

### Where we don't help

We say these on screen:
- **Falls out of view.** In another room or the bathroom, nothing alerts. One robot watches one room.
- **A dark room without a night-light.**
- **Someone who says "I'm OK" when they aren't.**
- **No internet.** The robot still beeps and the map still shows the alert over the local network, but texts and calls fail.

## Risks and fallbacks

| Risk | Fallback |
| --- | --- |
| YOLO is too slow on the Pi 5 | Image size 320, or MediaPipe for one person |
| Bluetooth drops | AUX through a USB sound card |
| The INMP441 arrives late | The webcam mic |
| The servos jitter | Hardware PWM, a deadband, freezing during the check-in |
| Twilio trial limits | Verify every number early |
| The venue Wi-Fi | Our own router |
| The live fall misfires on camera | The map's Trigger alert button runs the same pipeline |
| The routing server is down | The precomputed `routes.json`, then straight lines |

## Demo (3-minute Devpost video)

**Set.**
- A bedroom corner with an armchair and a crash mat. A teammate plays Ruth and really falls onto the mat; detection is live.
- The robot on a shelf.
- The laptop map on screen.
- Priya's phone, a neighbor's phone and the "911" phone, all in view.
- A screen recording of the live view: skeletons, the "Ruth" and "visitor" labels, and the state.

| Time | Beat |
| --- | --- |
| 0:00 | **Tom Notti** (20 s, cited). "Nothing told anyone he had fallen." |
| 0:20 | **Meet CareBot.** The head turns to follow Ruth, eyes blue. "Video never leaves this robot." The map shows her neighbors. |
| 0:35 | **Story 2.** The grandson naps and nothing happens. Ruth sits down hard, presses the button and says "I'm OK." Nothing is sent. "It doesn't cry wolf." |
| 1:00 | **Story 1.** A fall on the mat, two asks, silence. Beeping. Three neighbors light up on the map, Priya's phone rings, the 911 phone rings. The dispatcher asks "Is she breathing?" and hears: "I don't know. The device can't tell that. She hasn't responded since 2:07." |
| 1:40 | **The change moment.** A neighbor replies YES and their route draws on the map. Ruth hears that Daniel is coming. Daniel presses the button. "Who are you, and what's happening?" "I'm her neighbor, we're taking her to the hospital." The quote lands on the 911 phone, Priya's phone and the map. |
| 2:15 | **Stories 3 and 4**, side by side. A slump, then "help." A visitor falls, then "Is everyone OK?" |
| 2:30 | **Under the hood and proof.** Pose runs on the robot, the face lock, the honesty filter. Recall and false-alarm numbers, latency, red-team at zero. |
| 2:50 | **Close.** "Tom's phone was on the table, just out of reach. His neighbor had a key. CareBot tells them in under a minute." |

### Q&A prep

| Question | Answer |
| --- | --- |
| "Why not a pendant?" | A pendant has to be worn and pressed. In the BMJ study, alarms were mostly unused in the falls that led to long lies. CareBot needs nothing from Ruth. |
| "Isn't a camera invasive?" | Video and faces never leave the robot. Only events go over the network, and the live view can show the skeleton alone. |
| "Does it really call 911?" | In the demo it calls a teammate standing in for 911. In production this would be an NG911 data integration like RapidSOS. Apple Watch fall detection already calls 911 on its own. |
| "What if she says 'I'm OK' and isn't?" | We respect her answer. She stays in view, so another fall or stillness starts a new check-in. We list this under where we don't help. |
| "What if a stranger presses the button?" | We quote them; we don't vouch for them. Every update says "the device can't confirm who they are." |
| "What does it tell 911 that it can't know?" | Nothing. The red-team asks "Is she breathing?" in every state, and the answer is always "I don't know." |
| "What about the neighbors' privacy?" | Neighbors opt in as receivers. They get Ruth's block and a short message, never video. |
| "What if she's in another room?" | Nothing happens. One robot watches one room, and we say so. |
| "What if the internet goes down?" | The robot still beeps and the map still shows the alert over the local network. Texts and calls need the internet. |
| "What about a dark room?" | We need a night-light. A production version would use an IR camera. |

## Responsible design

- **Consent.** Faces are enrolled with consent, and neighbors opt in as receivers.
- **Privacy.** Video and faces stay on the robot. Only JSON events cross the network.
- **No medical guidance.** CareBot alerts people; it never diagnoses or interprets vitals.
- **Demo data.** The 911 line is a stand-in. Receivers in the demo are fictional or are teammates, and the map uses no real residents' addresses.

## Sources

- The Press Democrat (Mary Callahan, Feb 23, 2023): ["It's just sad because the whole thing was preventable": Cloverdale man, 92, lay for days after fall before being found](https://www.pressdemocrat.com/2023/02/23/its-just-sad-because-the-whole-thing-was-preventable-cloverdale-man-92-lay-for-days-after-fall-before-being-found/)
- Fleming J, Brayne C. [Inability to get up after falling, subsequent time on floor, and summoning help](https://www.bmj.com/content/337/bmj.a2227). BMJ 2008;337:a2227
- CDC: [Older Adult Falls Data](https://www.cdc.gov/falls/data-research/index.html)
- ACP Internist: [Falls leading to more deaths in adults ages 65 years and older](https://immattersacp.org/weekly/archives/2025/06/24/4.htm)
- AOL / UK coroner's Prevention of Future Deaths report: [Woman dies after being unable to reach emergency alarm at London care home](https://www.aol.co.uk/articles/concerns-raised-woman-dies-being-060028000.html)

## Credits

Ultralytics YOLO11 (AGPL-3.0), ByteTrack, OpenCV Zoo (YuNet, SFace), MediaPipe, Vosk, Piper, Twilio, Anthropic Claude, Eclipse Mosquitto and paho-mqtt, FastAPI, Flask, Leaflet, OpenStreetMap contributors, OSRM and FOSSGIS, the UR Fall Detection Dataset (URFD), and the Le2i Fall Detection Dataset.
