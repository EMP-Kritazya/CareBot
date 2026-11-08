"""CareBot, the whole loop: camera -> fall/stillness -> voice check-in -> escalation.

    python -m apps.watcher --webcam 1                    # laptop: mocks for hardware + alerts
    python -m apps.watcher --webcam 1 --demo             # short demo timers (for the video)
    python -m apps.watcher --webcam 1 --stillness 10     # every stillness timer = 10 s (quick test)
    python -m apps.watcher --webcam 1 --sim-neighbor 20  # a simulated neighbor says YES 20 s after alerting
    python -m apps.watcher --config config/pi.yaml --headless   # on the robot (Logitech webcam, SSH)
    python -m apps.watcher --webcam 1 --follow           # laptop: also run head-follow (angles printed)
    python -m apps.watcher --webcam 1 --twilio-dry-run   # print exactly what each call would say
    python -m apps.watcher --webcam 1 --twilio           # real calls (upgraded account; see docs/TWILIO.md)
    python -m apps.watcher --webcam 1 --map              # + the neighborhood map at http://localhost:8050
    python -m apps.watcher --config config/pi.yaml --headless   # the robot publishes to its own MQTT broker;
                                                         # the laptop runs: python -m dashboard.server

On the laptop CareBot really speaks (Mac voice) and really listens (Vosk on your mic). The beep,
eyes and button are terminal stand-ins, and alerts are printed instead of sent (Twilio is next).

Keys in the camera window (click it first), or type in the terminal + Enter (the only way when
headless; the live view is then at http://<robot>:8000, skeletons only unless --view camera):
    b   press the robot's button          t   trigger alert (same as the map's button)
    y   a neighbor replies YES            x   reset (closes the current incident)
    e   face saving (recalibrate / new)   q   quit
Anything else typed in the terminal while CareBot is listening counts as a spoken answer
(e.g. "I'm OK", "help", "I'm his neighbor, we're taking him to the hospital").
"""

from __future__ import annotations

import argparse
import sys
import threading
import time

from carebot.config import apply_stillness_flags, load_config
from carebot.core import state as st
from carebot.core.script import Profile
from carebot.ai.dispatcher import answer as answer_dispatcher
from carebot.ai.llm import NoAI, make_ai
from carebot.ai.phrasing import Phraser, fixed_lines
from carebot.ai.understand import Understander
from carebot.escalate.facts import fact_sheet
from carebot.escalate.notifier import ConsoleNotifier
from carebot.hw import make_hardware
from carebot.runtime import BUTTON, Runtime
from carebot.vision.follow import Follower, head_top, target_point
from carebot.vision.live import GREEN, RED, YELLOW, LiveVision, list_cameras
from carebot.voice.listen import make_listener
from carebot.voice.tts import make_tts
from carebot.voice.worker import VoiceWorker

HELP = "keys: b button | t trigger | y neighbor YES | x reset | e face | q quit | terminal: d <question> = 911 asks"


VISION_KEYS = set("enuamrs")


def console(runtime: Runtime, listener, quit_flag: threading.Event, neighbor: tuple[str, str | None],
            dispatcher=None, vision=None) -> None:
    """Terminal input: one-letter commands, `d <question>` (you play the 911 dispatcher), vision keys
    (face saving etc.), or a typed spoken answer while CareBot is listening."""
    for line in sys.stdin:
        text = line.strip()
        low = text.lower()
        if not text:
            continue
        if vision is not None and vision.face_mode is not None and (len(low) == 1 or low in ("esc", "escape")):
            vision.press(low)  # face saving keys: e / s / n / a / x / q
            continue
        if low == "q":
            quit_flag.set()
            return
        if low.startswith("d ") and dispatcher is not None:
            dispatcher(text[2:].strip())
            continue
        if command(low, runtime, neighbor):
            continue
        if vision is not None and len(low) == 1 and low in VISION_KEYS:
            vision.press(low)
            continue
        if not listener.type(text):
            print("  (CareBot isn't listening right now. Commands: b, t, y, x, e, q, d <question>)", flush=True)


def command(key: str, runtime: Runtime, neighbor: tuple[str, str | None]) -> bool:
    now = time.time()
    if key == "b":
        runtime.post(BUTTON)
    elif key == "t":
        runtime.post(st.ManualTrigger(now))
    elif key == "y":
        runtime.post(st.ReceiverReply(neighbor[0].lower(), neighbor[0], neighbor[1], "yes", now))
    elif key == "x":
        runtime.post(st.Reset(now))
    else:
        return False
    return True


def make_notifier(args, profile: dict, post, answer, cfg_twilio: dict | None = None):
    """Console (default), Twilio dry run, or real Twilio calls (+ the webhook server if there's a tunnel)."""
    if not (args.twilio or args.twilio_dry_run):
        return ConsoleNotifier(profile, post=post, sim_neighbor_after_s=args.sim_neighbor), None
    import os

    from carebot.config import load_env
    from carebot.escalate.twilio_calls import TwilioNotifier, check_account

    load_env()
    cfg_twilio = cfg_twilio or {}
    if args.twilio_dry_run:
        print("Twilio: DRY RUN. Calls are printed, nobody is called.", flush=True)
        return TwilioNotifier(profile, post, dry_run=True, family_via=cfg_twilio.get("family", "text"),
                              text_via=cfg_twilio.get("texts", "sms")), None
    problems = check_account()
    if problems:
        print("Twilio not ready, using the console instead:\n  - " + "\n  - ".join(problems), flush=True)
        return ConsoleNotifier(profile, post=post, sim_neighbor_after_s=args.sim_neighbor), None
    hooks = None
    public = os.environ.get("PUBLIC_BASE_URL", "").strip()
    if public:
        from carebot.escalate.twilio_hooks import TwilioHooks

        hooks = TwilioHooks(post, profile, os.environ["TWILIO_AUTH_TOKEN"], public, answer, port=args.hooks_port)
        problem = hooks.reachable_via(public)
        if problem:
            print(f"Twilio: real calls, but ONE-WAY: the tunnel check failed ({problem})", flush=True)
            hooks.close()
            return TwilioNotifier(profile, post, public_url=None, family_via=cfg_twilio.get("family", "text"),
                                  text_via=cfg_twilio.get("texts", "sms")), None
        print(f"Twilio: real calls. Tunnel OK: answers come back via {public} -> localhost:{hooks.port}", flush=True)
    else:
        print("Twilio: real calls, one-way (no PUBLIC_BASE_URL: no key presses or 911 questions). "
              "See docs/TWILIO.md for the tunnel.", flush=True)
    family_via = cfg_twilio.get("family", "text")
    return TwilioNotifier(profile, post, public_url=public or None, family_via=family_via,
                          text_via=cfg_twilio.get("texts", "sms")), hooks


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--camera", help="usb (the robot's Logitech webcam), a webcam index or /dev/videoN, or a video file "
                                     "(default: camera.source in the config)")
    ap.add_argument("--webcam", type=int, help="same as --camera N")
    ap.add_argument("--video")
    ap.add_argument("--headless", action="store_true", help="no window (the robot over SSH): keys from the terminal, "
                                                             "live view in the browser")
    ap.add_argument("--view", choices=("skeleton", "camera"),
                    help="browser view: skeleton (default when headless, privacy) or camera (setup)")
    ap.add_argument("--http-port", type=int, help="serve the live view on this port (default 8000 when headless)")
    ap.add_argument("--follow", action="store_true", help="run head-follow even with mock hardware (angles printed)")
    ap.add_argument("--config", default="config/mock.yaml")
    ap.add_argument("--demo", action="store_true",
                    help="short demo timers (sitting 30 s, standing 20 s, slumped 10 s, cooldown 60 s)")
    ap.add_argument("--stillness", type=float, metavar="SECONDS",
                    help="set every stillness timer to this many seconds (quick testing)")
    ap.add_argument("--sim-neighbor", type=float, metavar="SECONDS",
                    help="simulate the neighbors here; the ones who say YES do so this long after being texted")
    ap.add_argument("--map", action="store_true", help="run the neighborhood map in this process (laptop)")
    ap.add_argument("--map-port", type=int, default=8050)
    ap.add_argument("--mqtt", metavar="HOST", help="publish to this MQTT broker (default: transport in the config)")
    ap.add_argument("--no-mic", action="store_true", help="typed answers only")
    ap.add_argument("--no-ai", action="store_true", help="templates + keywords only (no Claude)")
    ap.add_argument("--twilio", action="store_true", help="real phone calls (needs an upgraded Twilio account)")
    ap.add_argument("--twilio-dry-run", action="store_true", help="build the calls and print them; call nobody")
    ap.add_argument("--hooks-port", type=int, default=8080, help="local port for Twilio webhooks (tunnel to it)")
    ap.add_argument("--speaker", help='speaker name (prefix), e.g. "MacBook Pro Speakers" (default: system output)')
    ap.add_argument("--mic", help='microphone name (part of it), e.g. "MacBook Pro Microphone" (default: system input)')
    ap.add_argument("--list-audio", action="store_true", help="list speakers and microphones, then exit")
    ap.add_argument("--size", help="camera resolution, e.g. 1280x720 (default: camera.size in the config, "
                                   "or the webcam's own)")
    ap.add_argument("--list-cameras", action="store_true")
    args = ap.parse_args()
    if args.list_cameras:
        list_cameras()
        return
    if args.list_audio:
        from carebot.voice.devices import list_audio
        list_audio()
        return

    cfg = load_config(args.config)
    apply_stillness_flags(cfg, demo=args.demo, override_s=args.stillness)
    profile = cfg.get("profile", {})
    voice_cfg = {**cfg.get("voice", {}), **({"mic": False} if args.no_mic else {})}
    if args.speaker:
        voice_cfg["output_device"] = args.speaker
    if args.mic:
        voice_cfg["input_device"] = args.mic
    from carebot.voice.devices import describe_audio
    for line in describe_audio(voice_cfg.get("output_device"), voice_cfg.get("input_device")):
        print(line, flush=True)
    check_in = cfg.get("check_in", {})

    hw = make_hardware(cfg["hardware"], cfg.get("hw"))
    cam_cfg = {**cfg.get("hw", {}).get("camera", {}), **cfg.get("camera", {})}
    camera = args.camera if args.camera is not None else (args.webcam if args.webcam is not None
                                                          else cam_cfg.get("source", "picamera" if cam_cfg.get("type") == "csi"
                                                                   else cam_cfg.get("index", 0)))
    http_port = args.http_port or (8000 if args.headless else None)
    vision = LiveVision(cfg, window="CareBot (q to quit)", camera=camera, video=args.video,
                        size=args.size or cam_cfg.get("size"), rotate=int(cam_cfg.get("rotate", 0)),
                        mirror=cam_cfg.get("mirror"),
                        headless=args.headless, view=args.view, http_port=http_port)

    # Head follow: pan follows Sandy; tilt (if enabled) only after tilt_dwell_s off-center vertically,
    # longer than a fall's drop window, so a fall is seen before the head could tilt with it. Freezes
    # during a possible fall, a check-in or an alert. Off with mock hardware unless --follow.
    follow_cfg = {**cfg.get("follow", {}), **({"fov_deg": cam_cfg["fov_deg"]} if "fov_deg" in cam_cfg else {})}
    drop_window = float(cfg["detectors"].get("fall", {}).get("drop_window_s", 1.2))
    if follow_cfg.get("tilt_enabled") and float(follow_cfg.get("tilt_dwell_s", 1.5)) <= drop_window:
        print(f"follow.tilt_dwell_s must be longer than fall.drop_window_s ({drop_window:g} s); "
              f"using {drop_window + 0.3:g} s so falls are seen first.", flush=True)
        follow_cfg["tilt_dwell_s"] = drop_window + 0.3
    follow_on = follow_cfg.get("enabled", cfg["hardware"] == "pi") or args.follow
    follower = Follower.from_config(follow_cfg)
    follow_log = open(follow_cfg["log"], "a", buffering=1) if follow_cfg.get("log") else None  # debugging
    if follow_log:
        print(f"Head follow log: {follow_cfg['log']}", flush=True)
    follow_last_log = [0.0]
    settle_s = cfg.get("hw", {}).get("pantilt", {}).get("settle_ms", 250) / 1000
    last_tilt_move = [-1e9]
    if follow_on:
        vision.camera_moving = lambda: (hw.pantilt.is_moving(), time.monotonic() - last_tilt_move[0] < settle_s)
        hw.pantilt.home()
    machine = st.Machine(Profile.from_config(cfg), asks=check_in.get("asks", 2))
    latest = {"frame": None}

    # Claude in the middle: phrasing + understanding. The state machine still decides everything.
    ai_cfg = cfg.get("ai", {})
    ai = NoAI() if args.no_ai else make_ai(cfg)
    names = tuple(n for n in (profile.get("resident_name"), profile.get("family_name"),
                              profile.get("neighbor_name")) if n)
    phraser = Phraser(ai, names, enabled=ai_cfg.get("polish", True))
    understander = Understander(ai, enabled=ai_cfg.get("understand", True))
    phraser.prewarm(fixed_lines(machine.p, profile.get("neighbor_name")))
    comfort = ai_cfg.get("comfort", {})

    runtime: Runtime
    listener = make_listener(voice_cfg)
    voice = VoiceWorker(make_tts(voice_cfg), listener, post=lambda e: runtime.post(e),
                        listen_s=check_in.get("listen_s", 15), phraser=phraser, understander=understander,
                        comfort_listen_s=comfort.get("listen_s", 10))
    notifier, hooks = make_notifier(args, profile, post=lambda e: runtime.post(e),
                                    answer=lambda inc_id, q, past: answer_for(inc_id, q, past),
                                    cfg_twilio=cfg.get("twilio", {}))
    runtime = Runtime(machine, hw, voice, notifier, posture=lambda: vision.resident_posture(latest["frame"]),
                      restart_stillness=vision.restart_stillness,
                      comfort_every_s=comfort.get("every_s", 30) if comfort.get("enabled", True) else None)

    # The map link: events out, Trigger / Reset / simulated replies in; neighbors in waves.
    from carebot.net.bridge import attach_map, run_sim_receivers
    from carebot.net.transport import make_transport
    transport = make_transport(cfg, args.mqtt)
    link = attach_map(runtime, notifier, transport, cfg, demo=args.demo)
    if hooks is not None:
        hooks.report = link.report  # real call / text progress (Twilio status callbacks) -> the map
    if link.waves is not None:
        print(f"Neighbors: {len(link.waves.ranked)} who agreed to help, in waves of {link.waves.wave_size} "
              f"every {link.waves.wave_s:g} s (nearest first). Map link: {transport.name}", flush=True)
    if args.map:
        from dashboard.server import serve_in_thread
        serve_in_thread(transport, cfg, port=args.map_port, yes_after_s=args.sim_neighbor)
        print(f"Map: http://localhost:{args.map_port}", flush=True)
    elif args.sim_neighbor is not None:
        run_sim_receivers(transport, yes_after_s=args.sim_neighbor)

    def camera_now(inc) -> str:
        """What the camera shows right now, about the person involved (not always Sandy), and how
        many people are in view. Facts only, for the 911 fact sheet."""
        frame = latest["frame"]
        seen = {"lying": "lying down", "sitting": "sitting", "upright": "upright",
                "out_of_view": "not visible to the camera"}
        n = len(frame.people) if frame is not None else 0
        name = profile.get("resident_name", "the resident")
        if inc.who == "resident":
            about = f"{name}: {seen.get(vision.resident_posture(frame), 'unclear')}"
        else:
            fell = vision.posture_of(frame, inc.track_id)
            about = (f"the person who fell (not {name}): {seen.get(fell, 'unclear')}"
                     + ("" if fell != "out_of_view" else " (the tracker may have lost them)"))
            if frame is not None and frame.resident_track_id is not None:
                about += f"; {name}: {seen.get(vision.resident_posture(frame), 'unclear')}"
        return f"{n} {'person' if n == 1 else 'people'} in view; {about} (as of {time.strftime('%-I:%M %p')})"

    def answer_for(incident_id: str, question: str, history: list | None = None) -> str:
        """The 911 dispatcher asked by phone: answer from this incident's facts only."""
        s = runtime.state
        inc = next((i for i in ([s.incident] if s.incident else []) + list(reversed(s.closed))
                    if i.id == incident_id), s.incident)
        if inc is None:
            return "I don't know. There is no active incident."
        facts = fact_sheet(inc, profile, time.time(), camera_now=camera_now(inc),
                           calls_911_back=bool(getattr(notifier, "psap_updates", False)))
        return answer_dispatcher(question, facts, ai, history)[0]

    def dispatcher(question: str) -> None:
        """You play the 911 dispatcher: CareBot answers from the fact sheet only."""
        inc = runtime.state.incident or (runtime.state.closed[-1] if runtime.state.closed else None)
        if inc is None:
            print("  (no incident yet: trigger one with t, or fall)", flush=True)
            return
        facts = fact_sheet(inc, profile, time.time(), camera_now=camera_now(inc))
        reply, source = answer_dispatcher(question, facts, ai)
        print(f"  911: {question}\n  CareBot ({source}): {reply}", flush=True)
    voice.start()
    voice.submit(st.Say("CareBot is on and watching."))  # sound check: if you don't hear this, check the volume

    quit_flag = threading.Event()
    neighbor = (profile.get("neighbor_name", "Krit"), profile.get("neighbor_street"))
    threading.Thread(target=console, args=(runtime, listener, quit_flag, neighbor, dispatcher, vision),
                     daemon=True).start()
    print(f"\nCareBot is watching. You should hear \"CareBot is on and watching.\" now; if not, check the "
          f"volume and sound output.\n{HELP}\n" + ("Head follow: on.\n" if follow_on else ""), flush=True)

    def follow(frame) -> None:
        """Keep Sandy in frame. Frozen while something is happening, so CareBot keeps watching the spot."""
        resident = frame.person(frame.resident_track_id)
        falling = any(t.state in ("candidate", "confirmed") for t in vision.detectors.fall.tracks.values())
        frozen = runtime.state.mode != "MONITORING" or falling or vision.resident_posture(frame) == "lying"
        target = target_point(resident) if resident else None
        top = head_top(resident) if resident else None
        now, pos = time.monotonic(), hw.pantilt.position()
        cmd = follower.update(now, target, pos, frozen, mirrored=vision.mirror, top=top)
        if follow_log and (cmd is not None or now - follow_last_log[0] >= 0.5):
            follow_last_log[0] = now
            seen = " ".join(f"#{p.track_id}@{t[0]:.2f},{t[1]:.2f}" for p in frame.people
                            if (t := target_point(p)) is not None)
            follow_log.write(f"{now:10.2f} mode={runtime.state.mode} frozen={frozen} resident={frame.resident_track_id} "
                             f"people=[{seen}] target={None if target is None else tuple(round(v, 2) for v in target)} "
                             f"top={None if top is None else round(top, 2)} pos=({pos[0]:.1f},{pos[1]:.1f}) cmd={cmd}\n")
        if cmd is None:
            return
        old_tilt = hw.pantilt.position()[1]
        if cmd[0] == "home":
            hw.pantilt.home()
        else:
            hw.pantilt.point(cmd[1], cmd[2])
        if hw.pantilt.position()[1] != old_tilt:
            last_tilt_move[0] = time.monotonic()

    try:
        while not quit_flag.is_set():
            tick = vision.step()
            runtime.pump()
            if tick.quit:
                break
            if tick.face_saving:
                vision.show(tick.image)
                continue
            latest["frame"] = tick.frame
            if follow_on:
                follow(tick.frame)
            for e in tick.events:
                if e.kind in ("fall", "stillness"):
                    runtime.post(st.Detected(e.kind, e.who, time.time(), e.track_id))
                elif e.kind == "got_up":
                    runtime.post(st.GotUp(time.time(), e.track_id))
                elif e.kind == "fall_recovered":
                    print(f"  fall recovered (track #{e.track_id}): logged on the robot only, nothing sent", flush=True)
            runtime.pump()

            status = runtime.status()
            extra = [status, f"AI: {ai.name}" + (f"  ({ai.typical_ms():.0f} ms typical)" if ai.typical_ms() else "")]
            if listener.listening.is_set():
                extra.append("LISTENING... (speak, or type in the terminal)")
            if runtime.last_said:
                extra.append(f'said: "{runtime.last_said[:90]}"')
            extra.append(HELP)
            vision.hud(tick.image, tick.frame, extra)
            if runtime.state.mode == "ALERTED":
                vision.cv2.rectangle(tick.image, (0, 0), (tick.image.shape[1] - 1, tick.image.shape[0] - 1), RED, 12)
            elif runtime.state.mode == "CHECK_IN":
                vision.cv2.rectangle(tick.image, (0, 0), (tick.image.shape[1] - 1, tick.image.shape[0] - 1), YELLOW, 8)
            key = vision.show(tick.image)
            if key in (ord("q"), 27):
                break
            if key != 255 and not command(chr(key), runtime, neighbor):
                vision.key(key, tick.frame)
    finally:
        if hooks is not None:
            hooks.close()
        voice.stop()
        hw.close()
        vision.close()
        print("CareBot stopped.", flush=True)


if __name__ == "__main__":
    main()
