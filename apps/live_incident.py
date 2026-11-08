"""A simulated incident with REAL phones: rigorous end-to-end test of the alert path.

    cloudflared tunnel --url http://localhost:8080          # terminal 1 (for the 911 Q&A + reports)
    python -m apps.live_incident --public-url https://<words>.trycloudflare.com

No camera, no fall: Sandy's fall and the two unanswered check-ins are simulated, then everything
after that is the real CareBot (state machine, runtime, fact sheet, Twilio):

  1. ALERT     FAMILY_PHONE gets the alert by SMS (or --family call/both).
               PSAP_STANDIN_PHONE gets the 911 call: the briefing, then you ask questions out loud.
  2. [Enter]   Krit says YES (simulated; or --neighbor call: NEIGHBOR_PHONE is called, press 1).
  3. [Enter]   Krit arrives, presses the button: "I'm his neighbor, we're taking him to the hospital."
               FAMILY_PHONE gets the update with that exact quote (and 911 too with --update-911).
  4. Summary of every SMS and call with Twilio's final status (delivered / blocked + reason).
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from carebot.ai.dispatcher import answer as answer_dispatcher
from carebot.ai.llm import NoAI, make_ai
from carebot.config import load_config, load_env
from carebot.core import state as st
from carebot.core.script import Profile
from carebot.escalate.facts import fact_sheet
from carebot.escalate.twilio_calls import TwilioNotifier, check_account
from carebot.escalate.twilio_hooks import TwilioHooks
from carebot.hw import make_hardware
from carebot.runtime import BUTTON, Runtime

QUIET = {p: {"verbose": False} for p in ("pantilt", "eyes", "buzzer", "button")}
BOLD, CYAN, RESET = "\033[1m", "\033[36m", "\033[0m"


def mask(n: str) -> str:
    return n[:2] + "*" * max(0, len(n) - 6) + n[-4:] if n else "(not set)"


class PrintVoice:
    """Speech is printed (no speaker or mic): the robot's side is simulated, the phones are real."""

    def __init__(self) -> None:
        self.last_ask: st.Ask | None = None

    def submit(self, a) -> None:
        if isinstance(a, st.Ask):
            self.last_ask = a


def step(title: str) -> None:
    print(f"\n{BOLD}== {title}{RESET}", flush=True)


def wait_enter(prompt: str) -> None:
    print(f"{CYAN}>> {prompt}  [press Enter]{RESET}", flush=True)
    sys.stdin.readline()


def run_for(runtime: Runtime, seconds: float, until=None) -> None:
    """Keep the runtime processing events (webhook answers, sends finishing) for a while."""
    end = time.time() + seconds
    while time.time() < end:
        runtime.pump()
        if until and until():
            return
        time.sleep(0.2)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--public-url", help="the tunnel URL (default: PUBLIC_BASE_URL in .env)")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--family", choices=("sms", "call", "both"), default="sms", help="how the family is told")
    ap.add_argument("--neighbor", choices=("sim", "call"), default="sim",
                    help="sim: Krit's YES is simulated; call: NEIGHBOR_PHONE is called (press 1)")
    ap.add_argument("--update-911", action="store_true", help="also call 911 with each update")
    ap.add_argument("--no-ai", action="store_true", help="911 answers without Claude")
    ap.add_argument("--dry-run", action="store_true", help="run every step, print the texts and calls, send nothing")
    args = ap.parse_args()

    load_env()
    cfg = load_config()
    profile = cfg["profile"]
    problems = check_account()
    if problems:
        raise SystemExit("Twilio not ready:\n  - " + "\n  - ".join(problems))
    public = (args.public_url or os.environ.get("PUBLIC_BASE_URL", "")).rstrip("/") or None
    ai = NoAI() if args.no_ai else make_ai(cfg)

    step("Setup")
    print(f"  family ({args.family}):       {mask(os.environ.get('FAMILY_PHONE', ''))}")
    print(f"  911 stand-in (call):  {mask(os.environ.get('PSAP_STANDIN_PHONE', ''))}")
    print(f"  neighbor ({args.neighbor}):     {mask(os.environ.get('NEIGHBOR_PHONE', ''))}")
    print(f"  calls/texts come from {mask(os.environ.get('TWILIO_FROM_NUMBER', ''))}   |   911 answers: {ai.name}")

    voice = PrintVoice()
    runtime: Runtime
    notifier = TwilioNotifier(profile, lambda e: runtime.post(e), public_url=public, family_via=args.family,
                              dry_run=args.dry_run,
                              psap_updates=args.update_911, include_neighbor_calls=args.neighbor == "call")
    runtime = Runtime(st.Machine(Profile.from_config(cfg)), make_hardware("mock", QUIET), voice, notifier,
                      posture=lambda: "lying")
    hooks = None
    if public:
        def answer_for(_inc: str, question: str, history: list) -> str:
            inc = runtime.state.incident or (runtime.state.closed[-1] if runtime.state.closed else None)
            facts = fact_sheet(inc, profile, time.time(), camera_now="lying on the floor (simulated)")
            reply, source = answer_dispatcher(question, facts, ai, history)
            print(f"     ({source})", flush=True)
            return reply

        hooks = TwilioHooks(runtime.post, profile, os.environ["TWILIO_AUTH_TOKEN"], public, answer_for, port=args.port)
        problem = hooks.reachable_via(public)
        if problem:
            hooks.close()
            raise SystemExit(f"Not starting: the tunnel check failed: {problem}")
        print(f"  tunnel OK: {public} -> localhost:{hooks.port} (911 questions and reports come back)")
    else:
        print("  no tunnel: the 911 call is one-way (briefing only), and no delivery reports")

    try:
        step("1. Simulated: Sandy falls and doesn't answer two check-ins")
        runtime.post(st.Detected("fall", "resident", time.time(), track_id=1))
        runtime.pump()
        for _ in range(2):
            runtime.post(st.Silence(voice.last_ask.ask_id, time.time()))
            runtime.pump()
        print("  ...sending the real alerts now")
        run_for(runtime, 25, until=lambda: runtime.state.incident and runtime.state.incident.alerts_sent)
        inc = runtime.state.incident
        print(f"  CareBot reached: {', '.join(inc.channels) or 'nobody'}  (it only says what really went out)")
        print(f"{CYAN}  Check FAMILY_PHONE for the text. Pick up the 911 call: after the briefing, ask questions "
              f"out loud (\"Is he breathing?\", \"Where is he?\", \"Who has a key?\"). Hang up when done.{RESET}")
        run_for(runtime, 5)

        if args.neighbor == "call":
            wait_enter("When the 911 call is done: CareBot will wait for the neighbor to press 1")
            run_for(runtime, 90, until=lambda: runtime.state.incident and runtime.state.incident.neighbor == "on_the_way")
        else:
            wait_enter("Step 2: Krit replies YES (simulated)")
            runtime.post(st.ReceiverReply("krit", profile.get("neighbor_name", "Krit"),
                                          profile.get("neighbor_street"), "yes", time.time()))
            runtime.pump()

        wait_enter("Step 3: Krit arrives, presses the button and says they're taking him to the hospital")
        runtime.post(BUTTON)
        runtime.pump()
        quote = "I'm his neighbor, we're taking him to the hospital now."
        print(f'  (simulated) Krit: "{quote}"')
        runtime.post(st.Reported("hospital", quote, "in_room", time.time(), voice.last_ask.ask_id))
        runtime.pump()
        print("  ...sending the update; waiting for delivery reports")
        run_for(runtime, 20)

        if args.dry_run:
            print("\n(dry run: nothing was sent)")
            return
        step("4. What Twilio says happened")
        for item in notifier.sent:
            if item["kind"] == "sms":
                m = notifier.client.messages(item["sid"]).fetch()
                extra = f"  error {m.error_code}" + (" = not registered for US texting (A2P 10DLC)"
                                                    if m.error_code == 30034 else "") if m.error_code else ""
                print(f"  SMS  {item['label']:24} {m.status.upper()}{extra}")
            else:
                c = notifier.client.calls(item["sid"]).fetch()
                print(f"  CALL {item['label']:24} {c.status.upper()}  ({c.duration or 0} s)")
        print("\nIf a text shows UNDELIVERED / error 30034: carriers block app texts until the number is registered "
              "(Console > Messaging > Regulatory compliance). Run again with --family call to use a call instead.")
    finally:
        if hooks:
            hooks.close()


if __name__ == "__main__":
    main()
