"""Answers coming back from calls: Twilio webhooks (key presses, spoken questions, call results).

    POST /twilio/neighbor   Krit pressed 1 (yes) or 2 (no)        -> ReceiverReply
    POST /twilio/psap       the dispatcher asked a question       -> a checked answer from the facts
    POST /twilio/status     a call finished: answered or missed   -> CallUpdate

Reachable from the internet through a tunnel (PUBLIC_BASE_URL), so every request must carry a valid
Twilio signature (X-Twilio-Signature, checked with the auth token); anything else gets 403.
The routing logic (`handle`) is pure and unit tested; the server is a thin wrapper.
"""

from __future__ import annotations

import secrets
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from carebot.core import state as st
from carebot.escalate.twilio_calls import ask_twiml, call_outcome, gate_answer, neighbor_reply_twiml

EMPTY = '<?xml version="1.0" encoding="UTF-8"?><Response/>'


def handle(path: str, query: dict, form: dict, post: Callable[[object], None], profile: dict,
           answer_question: Callable[[str, str, list], str] | None, public_url: str | None,
           history: dict | None = None) -> str:
    """Pure routing: returns the TwiML to send back, and posts events for the runtime.
    history: CallSid -> [(question, answer), ...] so the 911 agent remembers the conversation."""
    now = time.time()
    if path == "/twilio/start":
        speech, digits = (form.get("SpeechResult") or "").strip(), form.get("Digits", "")
        action = f"{public_url}/twilio/start?{urllib.parse.urlencode({'t': query.get('t', '')})}"
        twiml = gate_answer(query.get("t", ""), speech, digits, action)
        heard = f'"{speech}"' if speech else f"key {digits}" if digits else "nothing"
        print(f"  <- call picked up ({heard}) -> {'repeating the opening (screener/voicemail)' if 'Please pick up' in twiml else 'delivering the message'}",
              flush=True)
        return twiml
    if path == "/twilio/neighbor":
        twiml, reply = neighbor_reply_twiml(form.get("Digits", ""), profile.get("address"),
                                            profile.get("access_note"), profile.get("resident_name", "him"))
        if reply:
            name = query.get("name", "A neighbor")
            post(st.ReceiverReply(query.get("rid", name.lower()), name, profile.get("neighbor_street"), reply, now))
            print(f"  <- {name} pressed {form.get('Digits')}: {reply.upper()}", flush=True)
        return twiml
    if path == "/twilio/psap":
        question = (form.get("SpeechResult") or "").strip()
        action = f"{public_url}/twilio/psap?{urllib.parse.urlencode({'inc': query.get('inc', '')})}"
        if not question:
            return ask_twiml("I didn't catch that. You can ask me another question.", action)
        past = (history if history is not None else {}).setdefault(form.get("CallSid", "?"), [])
        reply = answer_question(query.get("inc", ""), question, list(past)) if answer_question else \
            "I don't know. The device only knows what its camera sees and what it hears."
        past.append((question, reply))
        print(f"  <- 911 asked: {question}\n  -> CareBot: {reply}", flush=True)
        return ask_twiml(reply + " Anything else?", action)
    if path == "/twilio/sms-status":
        status, code = form.get("MessageStatus", ""), form.get("ErrorCode", "")
        if status in ("delivered", "undelivered", "failed"):
            why = f" (error {code}{': not registered for US texting (A2P 10DLC)' if code == '30034' else ''})" if code else ""
            print(f"  <- SMS to {query.get('label', '?')}: {status.upper()}{why}", flush=True)
        return EMPTY
    if path == "/twilio/status":
        outcome = call_outcome(form.get("CallStatus", ""))
        target = query.get("target", "")
        if outcome and target in ("family", "911"):
            post(st.CallUpdate(target, outcome, now))
        if outcome:
            print(f"  <- call to {target}: {outcome}", flush=True)
        return EMPTY
    return EMPTY


class TwilioHooks:
    def __init__(self, post: Callable[[object], None], profile: dict, auth_token: str, public_url: str,
                 answer_question: Callable[[str, str], str] | None = None, port: int = 8080,
                 host: str = "127.0.0.1") -> None:
        from twilio.request_validator import RequestValidator

        validator = RequestValidator(auth_token)
        public = public_url.rstrip("/")
        history: dict = {}  # CallSid -> [(question, answer)]: the 911 agent's memory of each call

        self.token = secrets.token_hex(8)  # proves a health check reached *this* server
        token = self.token

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path.startswith("/health"):
                    body = f"carebot {token}".encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                parsed = urllib.parse.urlparse(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                form = dict(urllib.parse.parse_qsl(self.rfile.read(length).decode(), keep_blank_values=True))  # Twilio signs empty fields too
                signature = self.headers.get("X-Twilio-Signature", "")
                if not validator.validate(public + self.path, form, signature):
                    self.send_response(403)
                    self.end_headers()
                    return
                query = dict(urllib.parse.parse_qsl(parsed.query))
                body = handle(parsed.path, query, form, post, profile, answer_question, public, history).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/xml")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer((host, port), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True, name="twilio-hooks").start()

    def reachable_via(self, public_url: str, timeout: float = 8) -> str | None:
        """Fetch /health through the public URL. None if it reached this server, else the problem."""
        url = public_url.rstrip("/") + "/health"
        if not public_url.startswith("https://"):
            return f"{public_url} isn't an https URL (Twilio needs https)"
        if "developers.cloudflare.com" in public_url:
            return ("that's a Cloudflare documentation link, not your tunnel: copy the URL ending in "
                    ".trycloudflare.com from the box cloudflared prints")
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                body = r.read(200).decode(errors="replace")
        except Exception as e:  # noqa: BLE001
            return f"couldn't reach {url} ({e}). Is the tunnel running and pointing at localhost:{self.port}?"
        if body != f"carebot {self.token}":
            return f"{url} answered, but not with this CareBot server (wrong tunnel or port?)"
        return None

    def close(self) -> None:
        self.httpd.shutdown()
