"""Try one role's real phone call, end to end, without running the whole robot.

    python -m apps.call_test neighbor   # Krit: hears the alert, presses 1 (yes) or 2 (no)
    python -m apps.call_test family     # Suyog: hears the alert
    python -m apps.call_test 911        # the dispatcher: hears the briefing, then asks questions out loud

It builds a sample incident (Sandy fell and didn't answer two check-ins), places ONE call to that
role's number from .env, and prints what comes back (the key press, the dispatcher's questions and
CareBot's answers, whether the call was answered). For answers to come back, run the tunnel first
in another terminal and pass its URL (or set PUBLIC_BASE_URL in .env):

    cloudflared tunnel --url http://localhost:8080
    python -m apps.call_test neighbor --public-url https://<random-words>.trycloudflare.com
"""

from __future__ import annotations

import argparse
import os
import queue
import time

from carebot.ai.dispatcher import answer as answer_dispatcher
from carebot.ai.llm import NoAI, make_ai
from carebot.config import load_config, load_env
from carebot.core import state as st
from carebot.core.scenario import Scenario
from carebot.escalate.facts import briefing, fact_sheet
from carebot.escalate.notifier import alert_texts
from carebot.escalate.twilio_calls import (TwilioNotifier, check_account, neighbor_call_text, neighbor_twiml,
                                           psap_twiml, say_twiml)
from carebot.escalate.twilio_hooks import TwilioHooks

ROLES = {"neighbor": "NEIGHBOR_PHONE", "family": "FAMILY_PHONE", "911": "PSAP_STANDIN_PHONE"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("role", choices=sorted(ROLES))
    ap.add_argument("--public-url", help="the tunnel URL (default: PUBLIC_BASE_URL in .env)")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--wait", type=float, default=120, help="seconds to wait for answers")
    ap.add_argument("--no-ai", action="store_true", help="911 answers without Claude (offline rules)")
    args = ap.parse_args()

    load_env()
    cfg = load_config()
    profile = cfg["profile"]
    problems = check_account()
    if problems:
        raise SystemExit("Twilio not ready:\n  - " + "\n  - ".join(problems))
    public = (args.public_url or os.environ.get("PUBLIC_BASE_URL", "")).rstrip("/") or None

    # A sample incident: Sandy fell a minute ago and didn't answer two check-ins.
    s = Scenario()
    s.t = time.time() - 60
    s.fall()
    s.silence()
    s.silence()
    inc = s.incident
    facts = fact_sheet(inc, profile, time.time(), camera_now="lying down (sample incident)")
    ai = NoAI() if args.no_ai else make_ai(cfg)

    events: queue.Queue = queue.Queue()
    hooks = None
    if public:
        hooks = TwilioHooks(events.put, profile, os.environ["TWILIO_AUTH_TOKEN"], public,
                            answer_question=lambda _inc, q, past: answer_dispatcher(q, facts, ai, past)[0], port=args.port)
        problem = hooks.reachable_via(public)
        if problem:
            hooks.close()
            raise SystemExit(f"Not calling: the tunnel check failed: {problem}")
        print(f"Tunnel OK. Listening for answers: {public} -> localhost:{hooks.port}")
    else:
        print("No tunnel URL: the call is one-way (key presses and questions won't reach CareBot).")

    n = TwilioNotifier(profile, events.put, public_url=public)
    if args.role == "neighbor":
        twiml = neighbor_twiml(neighbor_call_text(profile, inc), n._url("/twilio/neighbor", inc=inc.id,
                                                                       rid="krit", name=profile.get("neighbor_name", "Krit")))
    elif args.role == "family":
        twiml = say_twiml(alert_texts(profile, inc.event, inc.who, inc.started_at)["family"])
    else:
        twiml = psap_twiml(briefing(facts), n._url("/twilio/psap", inc=inc.id))
    if not n._call(os.environ.get(ROLES[args.role], ""), twiml, args.role, args.role if args.role != "neighbor" else
                   "neighbor:krit", inc.id):
        raise SystemExit("The call wasn't placed.")

    if not public:
        return
    print(f"Pick up and follow the call. Waiting up to {args.wait:.0f} s for what comes back (Ctrl-C to stop)...")
    deadline = time.time() + args.wait
    try:
        while time.time() < deadline:
            try:
                e = events.get(timeout=1)
            except queue.Empty:
                continue
            if isinstance(e, st.ReceiverReply):
                said = ("CareBot would now tell Sandy: \"Krit from 115th Street is on the way.\""
                        if e.reply == "yes" else "CareBot would ask the next neighbor.")
                print(f"RESULT: {e.name} replied {e.reply.upper()}. {said}")
                time.sleep(4)  # let Twilio fetch the "thank you" reply before the server stops
                break
            elif isinstance(e, st.CallUpdate):
                print(f"RESULT: the call was {e.status}.")
                break
    except KeyboardInterrupt:
        pass
    finally:
        if hooks:
            hooks.close()


if __name__ == "__main__":
    main()
