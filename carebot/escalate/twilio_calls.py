"""Real phone calls through Twilio (voice-first). Same three methods as ConsoleNotifier.

    Krit (neighbor)   alert call: "...Press 1 if you can go now, 2 if you can't."  -> ReceiverReply
    Suyog (family)    alert call, then one call per update (quoting exactly what was said)
    911 stand-in      the fact-sheet briefing; then the dispatcher can ask questions out loud and
                      CareBot answers from the facts only (carebot/ai/dispatcher.py)

Answers coming back (key presses, the dispatcher's questions, answered/missed) need a public URL
(PUBLIC_BASE_URL, e.g. a free Cloudflare tunnel to `twilio_hooks`). Without one, calls still go out,
just one-way. Every spoken word is a template filled from the incident, or a checked answer.

Secrets come from .env only. Never call real 911: PSAP_STANDIN_PHONE is a teammate's phone.
"""

from __future__ import annotations

import os
import re
import secrets
import threading
import time
import urllib.parse
from typing import Callable

from carebot.core import state as st
from carebot.escalate.facts import briefing, fact_sheet
from carebot.escalate.notifier import alert_texts, got_up_family_text, update_text

YELLOW, DIM, RESET = "\033[33m", "\033[2m", "\033[0m"
VOICE = "Polly.Joanna"  # Amazon Polly voice through Twilio: clearer than the default


# ---------------------------------------------------------------- TwiML (pure)

def _vr():
    from twilio.twiml.voice_response import VoiceResponse

    return VoiceResponse()


def say_twiml(text: str, repeat: bool = True) -> str:
    """A one-way message, said twice (people miss the start of an unexpected call)."""
    vr = _vr()
    vr.say(text, voice=VOICE)
    if repeat:
        vr.pause(length=1)
        vr.say("Again: " + text, voice=VOICE)
    return str(vr)


def neighbor_twiml(text: str, action: str | None) -> str:
    """Alert for a neighbor. With a public URL, they answer with a key press."""
    if not action:
        return say_twiml(text)
    from twilio.twiml.voice_response import Gather

    vr = _vr()
    g = Gather(num_digits=1, action=action, method="POST", timeout=10)
    g.say(text + " Press 1 if you can go now. Press 2 if you can't.", voice=VOICE)
    vr.append(g)
    vr.say("I didn't get an answer. CareBot will ask someone else. Goodbye.", voice=VOICE)
    return str(vr)


def psap_twiml(opening: str, action: str | None) -> str:
    """911 stand-in: the briefing, then (with a public URL) spoken questions."""
    if not action:
        return say_twiml(opening)
    return ask_twiml(opening, action)


HOLD_LISTEN_S = 10          # each quiet listen while holding the line
HOLD_REMIND_EVERY = 3       # "still on the line" after this many quiet listens (about 30 s)
HOLD_MAX = 120              # about 20 minutes of total silence, then CareBot hangs up


def _with(url: str, **params) -> str:
    return url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)


def ask_twiml(text: str, action: str) -> str:
    """Say something, then listen for the dispatcher's next question. CareBot never hangs up on
    911 by itself: if nobody speaks, it keeps holding the line (see hold_twiml)."""
    from twilio.twiml.voice_response import Gather

    vr = _vr()
    g = Gather(input="speech", action=action, method="POST", speech_timeout="auto", timeout=8)
    g.say(text, voice=VOICE)
    vr.append(g)
    vr.redirect(_with(action, quiet=1), method="POST")
    return str(vr)


def hold_twiml(action: str, quiet: int) -> str:
    """The dispatcher is quiet: keep listening, now and then say CareBot is still there."""
    from twilio.twiml.voice_response import Gather

    vr = _vr()
    if quiet >= HOLD_MAX:
        vr.say("CareBot is hanging up now. It will call back with any update.", voice=VOICE)
        return str(vr)
    g = Gather(input="speech", action=action, method="POST", speech_timeout="auto", timeout=HOLD_LISTEN_S)
    if quiet % HOLD_REMIND_EVERY == 0:
        g.say("CareBot is still on the line. Ask me anything.", voice=VOICE)
    vr.append(g)
    vr.redirect(_with(action, quiet=quiet + 1), method="POST")
    return str(vr)


def neighbor_reply_twiml(digits: str, address: str | None, access: str | None,
                         resident: str = "him") -> tuple[str, str | None]:
    """(TwiML, reply) for a neighbor's key press. reply: "yes", "no" or None (any other key)."""
    vr = _vr()
    if digits == "1":
        where = f" The address is {address}." if address else ""
        key = f" {access}" if access else ""
        vr.say(f"Thank you. CareBot will tell {resident} you're coming.{where}{key} Goodbye.", voice=VOICE)
        return str(vr), "yes"
    if digits == "2":
        vr.say("Thank you. CareBot will ask someone else. Goodbye.", voice=VOICE)
        return str(vr), "no"
    vr.say("Sorry, I didn't understand. Goodbye.", voice=VOICE)
    return str(vr), None


WHATSAPP_SANDBOX = "+14155238886"  # Twilio's shared WhatsApp sandbox number (docs/TWILIO.md)

# Why a text didn't arrive, in plain words (Twilio error codes).
TEXT_ERRORS = {
    "30034": "not registered for US texting (A2P 10DLC)",
    "63015": "this phone hasn't joined the WhatsApp sandbox (send the join code, docs/TWILIO.md)",
    "63016": "outside WhatsApp's 24-hour window: that phone must message the sandbox first",
    "63007": "TWILIO_WHATSAPP_FROM isn't a WhatsApp sender on this account",
}

# Calls waiting for a person: token -> {"twiml": the real message, "intro": the opening line}.
PENDING: dict[str, dict] = {}
_PENDING_LOCK = threading.Lock()

SCREENER = re.compile(r"record your name|reason for calling|see if (this|the) person is available|not available|"
                      r"leave a message|after the (tone|beep)|voice ?mail|mailbox|person you are calling|"
                      r"screening|stay on the line|who's calling|who is calling", re.I)


def intro_line(target: str, profile: dict) -> str:
    """The opening that waits for a real person. Also what a phone's call screener shows."""
    name = profile.get("resident_name", "the resident")
    if target == "911":
        return (f"This is CareBot, an automated home safety device, calling with an emergency alert about {name}. "
                "Say hello or press any key to hear it.")
    return f"This is CareBot with an urgent alert about {name}. Say hello or press any key to hear it."


def gate_twiml(intro: str, start_action: str, voicemail: str) -> str:
    """Wait for a real person before delivering anything: twice ask for a key or "hello"; if nobody
    responds (voicemail), leave the full message."""
    from twilio.twiml.voice_response import Gather

    vr = _vr()
    vr.pause(length=1)
    for _ in range(2):
        g = Gather(input="dtmf speech", num_digits=1, timeout=6, speech_timeout="auto", action=start_action,
                   method="POST")
        g.say(intro, voice=VOICE)
        vr.append(g)
    vr.say(voicemail, voice=VOICE)
    return str(vr)


def gate_answer(token: str, speech: str, digits: str, start_action: str) -> str:
    """Pure. What to play after the opening got a response: the real message if a person answered,
    the opening again if it was a call screener or voicemail greeting."""
    with _PENDING_LOCK:
        entry = PENDING.get(token)
    if entry is None:
        vr = _vr()
        vr.say("This CareBot alert has expired. Goodbye.", voice=VOICE)
        return str(vr)
    if not digits and speech and SCREENER.search(speech):
        from twilio.twiml.voice_response import Gather

        vr = _vr()
        g = Gather(input="dtmf speech", num_digits=1, timeout=8, speech_timeout="auto", action=start_action,
                   method="POST")
        g.say(entry["intro"].replace("Say hello or press any key to hear it.", "Please pick up, then press any key."),
              voice=VOICE)
        vr.append(g)
        vr.say(entry.get("voicemail", ""), voice=VOICE)
        return str(vr)
    return entry["twiml"]


CALL_PROGRESS = {"queued": "dialing", "initiated": "dialing", "ringing": "ringing", "in-progress": "connected",
                 "completed": "ended", "busy": "no_answer", "no-answer": "no_answer", "failed": "failed",
                 "canceled": "failed"}
TEXT_PROGRESS = {"queued": "sending", "accepted": "sending", "sent": "sent", "delivered": "delivered", "read": "read",
                 "undelivered": "failed", "failed": "failed"}


def call_outcome(call_status: str) -> str | None:
    """Twilio's final CallStatus -> "answered" / "unanswered" (None while still in progress)."""
    if call_status == "completed":
        return "answered"
    if call_status in ("busy", "no-answer", "failed", "canceled"):
        return "unanswered"
    return None


def neighbor_call_text(profile: dict, incident: st.Incident) -> str:
    """What a neighbor hears (facts only); the key-press prompt is added by neighbor_twiml."""
    texts = alert_texts(profile, incident.event, incident.who, incident.started_at)
    return texts["neighbors"].split(" Reply YES")[0].strip()


# ---------------------------------------------------------------- the notifier

class TwilioNotifier:
    """Places real calls. Calls go out on a background thread so the camera loop never waits."""

    def __init__(self, profile: dict, post: Callable[[object], None], client=None, env: dict | None = None,
                 public_url: str | None = None, dry_run: bool = False, facts_now=None,
                 family_via: str = "call", psap_updates: bool = True, include_neighbor_calls: bool = True,
                 text_via: str = "sms") -> None:
        env = env if env is not None else os.environ
        self.profile, self.post = profile, post
        self.from_number = env.get("TWILIO_FROM_NUMBER", "")
        self.family = env.get("FAMILY_PHONE", "")
        self.psap = env.get("PSAP_STANDIN_PHONE", "")
        names = [n.strip() for n in profile.get("neighbor_name", "Krit").split(",")]
        phones = [p.strip() for p in env.get("NEIGHBOR_PHONE", "").split(",") if p.strip()]
        self.neighbor_phones = [(n, p) for n, p in zip(names + [f"Neighbor {i + 2}" for i in range(len(phones))], phones)]
        self.public = (public_url or env.get("PUBLIC_BASE_URL", "") or "").rstrip("/") or None
        self.dry_run = dry_run
        self.family_via = family_via            # "text" | "call" | "both"  (a text goes by text_via; "sms" = "text")
        self.text_via = text_via                # "sms" | "whatsapp": how texts go out
        # The WhatsApp sender: Twilio's shared sandbox number unless you have your own WhatsApp sender.
        self.whatsapp_from = env.get("TWILIO_WHATSAPP_FROM", "") or WHATSAPP_SANDBOX
        self.psap_updates = psap_updates        # call 911 again for each update
        self.include_neighbor_calls = include_neighbor_calls
        self.sent: list[dict] = []              # every call/SMS placed: {kind, label, sid}, for test reports
        self.paused = False
        self.neighbors = None                   # Waves: neighbors in waves (else every NEIGHBOR_PHONE at once)
        self.psap_calls: dict[str, str] = {}   # incident id -> the 911 call's sid (updates go into it)
        self.last_sid: str | None = None
        self.report = lambda **kw: None         # live delivery status for the map (carebot/net/bridge.py)
        self.facts_now = facts_now  # () -> fact sheet for the current incident (911 Q&A)
        self.client = client
        if self.client is None and not dry_run:
            from twilio.rest import Client

            self.client = Client(env["TWILIO_ACCOUNT_SID"], env["TWILIO_AUTH_TOKEN"])

    # ---- URLs for answers coming back

    def _url(self, path: str, **params) -> str | None:
        if not self.public:
            return None
        return f"{self.public}{path}?{urllib.parse.urlencode(params)}" if params else f"{self.public}{path}"

    # ---- placing calls

    def _call(self, to: str, twiml: str, label: str, target: str, incident_id: str, voicemail: str = "",
              kind: str = "alert") -> bool:
        """True if Twilio accepted the call. With a tunnel, the call first waits for a real person
        (not a call screener or voicemail) before delivering `twiml`; `voicemail` is left otherwise."""
        if not to:
            print(f"{YELLOW}  -> {label}: no phone number set in .env, skipped{RESET}", flush=True)
            return False
        if self.public:
            token = secrets.token_urlsafe(8)
            kind = "911" if target == "911" else target.split(":")[0]
            intro = intro_line(kind, self.profile)
            with _PENDING_LOCK:
                PENDING[token] = {"twiml": twiml, "intro": intro, "voicemail": voicemail or intro}
            twiml = gate_twiml(intro, self._url("/twilio/start", t=token), voicemail or intro)
        if self.dry_run:
            print(f"{YELLOW}  -> CALL {label} (dry run){RESET}\n{DIM}{twiml}{RESET}", flush=True)
            return True
        kwargs = dict(to=to, from_=self.from_number, twiml=twiml)
        status = self._url("/twilio/status", inc=incident_id, target=target, kind=kind)
        if status:  # every step, so the map shows dialing -> ringing -> connected -> ended
            kwargs.update(status_callback=status, status_callback_method="POST",
                          status_callback_event=["initiated", "ringing", "answered", "completed"])
        shown = target if target in ("911", "family") else None
        try:
            call = self.client.calls.create(**kwargs)
            self.last_sid = call.sid
            self.sent.append({"kind": "call", "label": label, "sid": call.sid})
            print(f"{YELLOW}  -> calling {label}{RESET} {DIM}({call.sid[-6:]}){RESET}", flush=True)
            if shown:
                self.report(target=shown, kind=kind, channel="call", status="dialing")
            return True
        except Exception as e:  # noqa: BLE001 - one failed call must not stop the others
            print(f"{YELLOW}  -> call to {label} FAILED: {e}{RESET}", flush=True)
            if shown:
                self.report(target=shown, kind=kind, channel="call", status="failed")
            return False

    def _sms(self, to: str, body: str, label: str) -> bool:
        """A text, by SMS or WhatsApp (text_via). True if Twilio accepted it (delivery is reported
        later: see the sms-status webhook). SMS from a US long code needs A2P 10DLC registration, or
        carriers block it (30034). WhatsApp (sandbox) needs the recipient to have joined it."""
        channel = "WhatsApp" if self.text_via == "whatsapp" else "SMS"
        if not to:
            print(f"{YELLOW}  -> {channel} {label}: no phone number set in .env, skipped{RESET}", flush=True)
            return False
        if self.dry_run:
            print(f"{YELLOW}  -> {channel} {label} (dry run):{RESET} {body}", flush=True)
            return True
        if self.text_via == "whatsapp":
            kwargs = dict(to=f"whatsapp:{to}", from_=f"whatsapp:{self.whatsapp_from}", body=body)
        else:
            kwargs = dict(to=to, from_=self.from_number, body=body)
        kind = "update" if "update" in label else "alert"
        status = self._url("/twilio/sms-status", label=label, target="family", kind=kind, channel=channel.lower())
        if status:
            kwargs["status_callback"] = status
        try:
            msg = self.client.messages.create(**kwargs)
            self.sent.append({"kind": "sms", "label": label, "sid": msg.sid, "channel": channel})
            print(f"{YELLOW}  -> {channel} to {label}:{RESET} {body}", flush=True)
            self.report(target="family", kind=kind, channel=channel.lower(), status="sending")
            return True
        except Exception as e:  # noqa: BLE001
            print(f"{YELLOW}  -> {channel} to {label} FAILED: {e}{RESET}", flush=True)
            self.report(target="family", kind=kind, channel=channel.lower(), status="failed")
            return False

    def _family(self, text: str, label: str, incident_id: str) -> bool:
        """Tell the family by text and/or call, as configured. True if any of it went out."""
        ok = False
        if self.family_via in ("text", "sms", "both"):
            ok |= self._sms(self.family, text, label)
        if self.family_via in ("call", "both"):
            ok |= self._call(self.family, say_twiml(text), label, "family", incident_id, voicemail=text,
                             kind="update" if "update" in label else "alert")
        return ok

    def escalate(self, incident: st.Incident, include_neighbors: bool, family_only: bool = False) -> None:
        threading.Thread(target=self._escalate, args=(incident, include_neighbors, family_only), daemon=True).start()

    def _escalate(self, incident: st.Incident, include_neighbors: bool, family_only: bool) -> None:
        if family_only:
            ok = self._family(got_up_family_text(self.profile, incident), "Suyog (family only)", incident.id)
            self.post(st.EscalationSent(("family",) if ok else (), time.time()))
            return
        texts = alert_texts(self.profile, incident.event, incident.who, incident.started_at)
        channels = []
        if include_neighbors and self.neighbors is not None:
            if self.neighbors.start(incident):   # waves: real phones are called through call_receiver
                channels.append("neighbors")
        elif include_neighbors and self.include_neighbor_calls:
            reached = False
            for name, phone in self.neighbor_phones:
                reached |= self.call_neighbor(name, phone, incident)
            if reached:
                channels.append("neighbors")
        opening = briefing(fact_sheet(incident, self.profile, time.time()))
        self.last_sid = None
        if self._call(self.psap, psap_twiml(opening, self._url("/twilio/psap", inc=incident.id)),
                      "911 stand-in", "911", incident.id, voicemail=opening):
            channels.append("911")
            if self.last_sid:
                self.psap_calls[incident.id] = self.last_sid
        # The family is called last, so their message can say truthfully who was already reached.
        family = alert_texts(self.profile, incident.event, incident.who, incident.started_at,
                             contacted=tuple(channels))["family"]
        if self._family(family, "Suyog (family)", incident.id):
            channels.append("family")
        self.post(st.EscalationSent(tuple(channels), time.time()))  # CareBot only says what was sent

    def call_neighbor(self, name: str, phone: str, incident: st.Incident, rid: str | None = None) -> bool:
        text = neighbor_call_text(self.profile, incident)
        rid = rid or name.lower()
        action = self._url("/twilio/neighbor", inc=incident.id, rid=rid, name=name)
        return self._call(phone, neighbor_twiml(text, action), f"{name} (neighbor)", f"neighbor:{rid}",
                          incident.id, voicemail=text)

    def call_receiver(self, r, incident: st.Incident) -> bool:
        """For Waves: a receiver with a real phone (its number is in .env under r.phone_env)."""
        phone = os.environ.get(r.phone_env or "", "").split(",")[0].strip()
        return self.call_neighbor(r.name, phone, incident, rid=r.id) if phone else False

    def update(self, u: st.SendUpdate) -> None:
        text = update_text(self.profile, u.status, u.quote, u.source, time.time(), u.helper, u.street)

        def run():
            self._family(text, "Suyog (update)", u.incident_id)
            if self.psap_updates:
                self._update_911(u.incident_id, text)

        threading.Thread(target=run, daemon=True).start()

    def _update_911(self, incident_id: str, text: str) -> None:
        """Into the 911 call if it's still open (the dispatcher hears it at once and can ask more);
        otherwise call 911 again, and that call also stays open for questions."""
        action = self._url("/twilio/psap", inc=incident_id)
        sid = self.psap_calls.get(incident_id)
        if sid and action and not self.dry_run:
            try:
                if self.client.calls(sid).fetch().status == "in-progress":
                    self.client.calls(sid).update(twiml=ask_twiml("CareBot update. " + text + " Anything else?", action))
                    print(f"{YELLOW}  -> update spoken into the open 911 call{RESET} {DIM}({sid[-6:]}){RESET}", flush=True)
                    self.report(target="911", kind="update", channel="call", status="delivered")
                    return
            except Exception as e:  # noqa: BLE001 - fall back to a new call
                print(f"{YELLOW}  -> couldn't reach the open 911 call ({e}); calling again{RESET}", flush=True)
        self.last_sid = None
        if self._call(self.psap, psap_twiml("CareBot update. " + text, action), "911 stand-in (update)", "911",
                      incident_id, voicemail=text, kind="update") and self.last_sid:
            self.psap_calls[incident_id] = self.last_sid

    def pause(self) -> None:
        self.paused = True
        if self.neighbors is not None:
            self.neighbors.pause()

    def resume(self) -> None:
        self.paused = False
        if self.neighbors is not None:
            self.neighbors.resume()


def check_account(env: dict | None = None) -> list[str]:
    """Read-only startup checks. Returns problems (empty = ready). Nothing is sent."""
    env = env if env is not None else os.environ
    missing = [k for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER") if not env.get(k)]
    if missing:
        return [f"missing in .env: {', '.join(missing)}"]
    problems = []
    try:
        from twilio.rest import Client

        c = Client(env["TWILIO_ACCOUNT_SID"], env["TWILIO_AUTH_TOKEN"])
        acct = c.api.accounts(env["TWILIO_ACCOUNT_SID"]).fetch()
        if acct.type == "Trial":
            problems.append("the account is still a trial: calls can't carry CareBot's words until you upgrade")
        owned = {n.phone_number for n in c.incoming_phone_numbers.list(limit=20)}
        if env["TWILIO_FROM_NUMBER"] not in owned:
            problems.append("TWILIO_FROM_NUMBER isn't a number this account owns (buy one: Phone Numbers > Buy a number)")
    except Exception as e:  # noqa: BLE001
        problems.append(f"couldn't reach Twilio ({e})")
    for k in ("FAMILY_PHONE", "NEIGHBOR_PHONE", "PSAP_STANDIN_PHONE"):
        if not env.get(k):
            problems.append(f"{k} is empty in .env")
    return problems
