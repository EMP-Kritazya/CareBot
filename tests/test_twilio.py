"""Twilio voice calls with a fake client: what each person hears, replies coming back, honesty."""

import time

from carebot.config import load_config
from carebot.core import state as st
from carebot.core.scenario import Scenario
from carebot.escalate.twilio_calls import TwilioNotifier, call_outcome, neighbor_twiml, psap_twiml
from carebot.escalate.twilio_hooks import handle

PROFILE = load_config("config/mock.yaml")["profile"]
ENV = {"TWILIO_FROM_NUMBER": "+15550000000", "FAMILY_PHONE": "+15550000001",
       "NEIGHBOR_PHONE": "+15550000002", "PSAP_STANDIN_PHONE": "+15550000003"}


class FakeCalls:
    def __init__(self, fail_to=()):
        self.made, self.fail_to = [], set(fail_to)

    def create(self, **kw):
        if kw["to"] in self.fail_to:
            raise RuntimeError("unreachable")
        self.made.append(kw)
        return type("Call", (), {"sid": "CA" + "0" * 30 + str(len(self.made))})()


class FakeClient:
    def __init__(self, fail_to=()):
        self.calls = FakeCalls(fail_to)


def alerted_incident():
    s = Scenario()
    s.t = time.time() - 60
    s.fall()
    s.silence()
    s.silence()
    return s.incident


def run_escalation(public=None, fail_to=()):
    posted = []
    client = FakeClient(fail_to)
    n = TwilioNotifier(PROFILE, posted.append, client=client, env=ENV, public_url=public)
    n._escalate(alerted_incident(), include_neighbors=True, family_only=False)  # synchronously, for the test
    return client.calls.made, posted


def _real_message(call: dict) -> str:
    """With a tunnel, a call opens with the wait-for-a-person gate; the real message is behind it."""
    import re

    from carebot.escalate.twilio_calls import PENDING

    token = re.search(r"/twilio/start\?t=([\w-]+)", call["twiml"]).group(1)
    return PENDING[token]["twiml"]


def test_everyone_is_called_with_carebots_words():
    made, posted = run_escalation(public="https://x.trycloudflare.com")
    by_to = {c["to"]: c for c in made}
    assert set(by_to) == {ENV["NEIGHBOR_PHONE"], ENV["FAMILY_PHONE"], ENV["PSAP_STANDIN_PHONE"]}
    assert all(c["from_"] == ENV["TWILIO_FROM_NUMBER"] for c in made)
    for c in made:  # nothing is said until a person responds
        assert "Say hello or press any key" in c["twiml"] and "/twilio/start" in c["twiml"]
    assert "Press 1 if you can go now" in _real_message(by_to[ENV["NEIGHBOR_PHONE"]])
    assert "Sandy may have fallen" in _real_message(by_to[ENV["FAMILY_PHONE"]])
    psap = _real_message(by_to[ENV["PSAP_STANDIN_PHONE"]])
    assert "This is CareBot, an automated home safety device" in psap and "<Gather" in psap
    assert isinstance(posted[-1], st.EscalationSent) and set(posted[-1].channels) == {"neighbors", "family", "911"}


def test_without_a_tunnel_calls_are_one_way():
    made, _ = run_escalation(public=None)
    assert all("<Gather" not in c["twiml"] and "status_callback" not in c for c in made)


def test_carebot_only_claims_the_calls_that_went_out():
    _, posted = run_escalation(fail_to={ENV["PSAP_STANDIN_PHONE"]})
    assert set(posted[-1].channels) == {"neighbors", "family"}


def test_family_only_calls_only_suyog():
    posted = []
    client = FakeClient()
    n = TwilioNotifier(PROFILE, posted.append, client=client, env=ENV)
    inc = alerted_incident()
    n._escalate(inc, include_neighbors=False, family_only=True)
    assert [c["to"] for c in client.calls.made] == [ENV["FAMILY_PHONE"]]
    assert posted[-1].channels == ("family",)


def test_dry_run_calls_nobody(capsys):
    posted = []
    n = TwilioNotifier(PROFILE, posted.append, env=ENV, dry_run=True)
    n._escalate(alerted_incident(), include_neighbors=True, family_only=False)
    assert "dry run" in capsys.readouterr().out
    assert set(posted[-1].channels) == {"neighbors", "family", "911"}


def test_twiml_escapes_and_asks_for_keys():
    t = neighbor_twiml("Sandy & co <test>", "https://x/twilio/neighbor?inc=1")
    assert "&amp;" in t and "&lt;test&gt;" in t and 'numDigits="1"' in t
    assert "<Gather" in psap_twiml("Briefing.", "https://x/twilio/psap?inc=1")


# ---------------------------------------------------------------- answers coming back

def test_krit_presses_1_and_carebot_hears_yes():
    posted = []
    twiml = handle("/twilio/neighbor", {"rid": "krit", "name": "Krit"}, {"Digits": "1"}, posted.append,
                   PROFILE, None, "https://x")
    assert isinstance(posted[0], st.ReceiverReply) and posted[0].reply == "yes" and posted[0].name == "Krit"
    assert "Krit has a key" in twiml and "115th Street" in twiml


def test_krit_presses_2_is_a_no_and_other_keys_are_ignored():
    posted = []
    handle("/twilio/neighbor", {"name": "Krit"}, {"Digits": "2"}, posted.append, PROFILE, None, "https://x")
    handle("/twilio/neighbor", {"name": "Krit"}, {"Digits": "9"}, posted.append, PROFILE, None, "https://x")
    assert [p.reply for p in posted] == ["no"]


def test_dispatcher_question_gets_an_answer_and_another_listen():
    asked = []
    twiml = handle("/twilio/psap", {"inc": "inc-1"}, {"SpeechResult": "Is he breathing?"}, lambda e: None,
                   PROFILE, lambda inc, q, past: asked.append((inc, q)) or "I don't know.", "https://x")
    assert asked == [("inc-1", "Is he breathing?")]
    assert "I don't know. Anything else?" in twiml and "<Gather" in twiml


def test_call_results_reach_the_state_machine():
    posted = []
    handle("/twilio/status", {"target": "911"}, {"CallStatus": "no-answer"}, posted.append, PROFILE, None, None)
    handle("/twilio/status", {"target": "family"}, {"CallStatus": "completed"}, posted.append, PROFILE, None, None)
    assert [(p.target, p.status) for p in posted] == [("911", "unanswered"), ("family", "answered")]
    assert call_outcome("ringing") is None


def test_unsigned_webhook_requests_are_rejected():
    import urllib.error
    import urllib.request

    from carebot.escalate.twilio_hooks import TwilioHooks

    hooks = TwilioHooks(lambda e: None, PROFILE, "secret-token", "https://x.example", port=0)
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{hooks.port}/twilio/neighbor?name=Krit",
                                     data=b"Digits=1", method="POST")
        try:
            urllib.request.urlopen(req, timeout=3)
            assert False, "an unsigned request was accepted"
        except urllib.error.HTTPError as e:
            assert e.code == 403
    finally:
        hooks.close()


def test_signed_webhook_request_is_accepted_and_posts_yes():
    import urllib.request

    from twilio.request_validator import RequestValidator

    from carebot.escalate.twilio_hooks import TwilioHooks

    posted = []
    hooks = TwilioHooks(posted.append, PROFILE, "secret-token", "https://x.example", port=0)
    try:
        path = "/twilio/neighbor?inc=inc-1&rid=krit&name=Krit"
        sig = RequestValidator("secret-token").compute_signature("https://x.example" + path, {"Digits": "1"})
        req = urllib.request.Request(f"http://127.0.0.1:{hooks.port}{path}", data=b"Digits=1", method="POST",
                                     headers={"X-Twilio-Signature": sig,
                                              "Content-Type": "application/x-www-form-urlencoded"})
        body = urllib.request.urlopen(req, timeout=3).read().decode()
        assert "Thank you" in body and posted and posted[0].reply == "yes"
    finally:
        hooks.close()



def test_family_hears_only_who_was_actually_reached():
    made, _ = run_escalation(fail_to={ENV["PSAP_STANDIN_PHONE"]})
    family = next(c for c in made if c["to"] == ENV["FAMILY_PHONE"])["twiml"]
    assert "Neighbors have been contacted." in family and "911" not in family
    made, _ = run_escalation()
    family = next(c for c in made if c["to"] == ENV["FAMILY_PHONE"])["twiml"]
    assert "Neighbors and 911 have been contacted." in family


def test_tunnel_check_catches_a_docs_link_and_an_unreachable_url():
    from carebot.escalate.twilio_hooks import TwilioHooks

    hooks = TwilioHooks(lambda e: None, PROFILE, "t", "https://x.example", port=0)
    try:
        assert "documentation link" in hooks.reachable_via(
            "https://developers.cloudflare.com/cloudflare-one/connections/connect-apps")
        assert "https" in hooks.reachable_via("http://insecure.example")
        assert "couldn't reach" in hooks.reachable_via("https://127.0.0.1:1", timeout=1)
        # Reaching itself directly (stands in for a working tunnel): the token matches.
        import urllib.request
        body = urllib.request.urlopen(f"http://127.0.0.1:{hooks.port}/health", timeout=3).read().decode()
        assert body == f"carebot {hooks.token}"
    finally:
        hooks.close()


def test_signature_includes_empty_fields_like_twilio_sends():
    """Live bug: Twilio's real request has empty fields (FromZip=, CallerState=...). Dropping them
    broke the signature check and every key press got 403 ("application error")."""
    import urllib.parse
    import urllib.request

    from twilio.request_validator import RequestValidator

    from carebot.escalate.twilio_hooks import TwilioHooks

    posted = []
    hooks = TwilioHooks(posted.append, PROFILE, "secret-token", "https://x.example", port=0)
    try:
        path = "/twilio/neighbor?inc=inc-1&rid=krit&name=Krit"
        form = {"Digits": "1", "FromZip": "", "CallerState": "", "AccountSid": "AC123", "CallStatus": "in-progress"}
        sig = RequestValidator("secret-token").compute_signature("https://x.example" + path, form)
        req = urllib.request.Request(f"http://127.0.0.1:{hooks.port}{path}",
                                     data=urllib.parse.urlencode(form).encode(), method="POST",
                                     headers={"X-Twilio-Signature": sig,
                                              "Content-Type": "application/x-www-form-urlencoded"})
        assert "Thank you" in urllib.request.urlopen(req, timeout=3).read().decode()
        assert posted and posted[0].reply == "yes"
    finally:
        hooks.close()


class FakeMessages:
    def __init__(self):
        self.sent = []

    def create(self, **kw):
        self.sent.append(kw)
        return type("Msg", (), {"sid": "SM" + "0" * 30 + str(len(self.sent))})()


def test_family_by_sms_gets_the_same_honest_text():
    posted = []
    client = FakeClient()
    client.messages = FakeMessages()
    n = TwilioNotifier(PROFILE, posted.append, client=client, env=ENV, family_via="sms")
    n._escalate(alerted_incident(), include_neighbors=True, family_only=False)
    assert [m["to"] for m in client.messages.sent] == [ENV["FAMILY_PHONE"]]
    assert "Sandy may have fallen" in client.messages.sent[0]["body"]
    assert ENV["FAMILY_PHONE"] not in [c["to"] for c in client.calls.made]  # texted, not called
    assert "family" in posted[-1].channels


def test_update_by_sms_quotes_exactly_and_911_update_is_optional():
    import threading
    client = FakeClient()
    client.messages = FakeMessages()
    n = TwilioNotifier(PROFILE, lambda e: None, client=client, env=ENV, family_via="sms", psap_updates=False)
    n.update(st.SendUpdate("inc-1", "hospital", "we're taking him to the hospital", "in_room"))
    for t in threading.enumerate():
        if t is not threading.current_thread() and t.daemon:
            t.join(timeout=2)
    assert "\"we're taking him to the hospital\"" in client.messages.sent[0]["body"]
    assert client.calls.made == []


def test_sms_delivery_report_explains_a_block(capsys):
    handle("/twilio/sms-status", {"label": "Suyog (family)"}, {"MessageStatus": "undelivered", "ErrorCode": "30034"},
           lambda e: None, PROFILE, None, None)
    assert "UNDELIVERED" in capsys.readouterr().out



# ---------------------------------------------------------------- wait for a real person (live feedback)

def test_call_screener_gets_the_opening_again_not_the_message():
    from carebot.escalate.twilio_calls import PENDING

    PENDING["tok1"] = {"twiml": "<Response><Say>REAL MESSAGE</Say></Response>", "intro": "This is CareBot with an "
                       "urgent alert about Sandy. Say hello or press any key to hear it.", "voicemail": "VM"}
    screener = handle("/twilio/start", {"t": "tok1"},
                      {"SpeechResult": "Hi, if you record your name and reason for calling, I'll see if this person "
                                       "is available."}, lambda e: None, PROFILE, None, "https://x")
    assert "REAL MESSAGE" not in screener and "Please pick up" in screener
    person = handle("/twilio/start", {"t": "tok1"}, {"SpeechResult": "Hello?"}, lambda e: None, PROFILE, None, "https://x")
    assert "REAL MESSAGE" in person
    key = handle("/twilio/start", {"t": "tok1"}, {"Digits": "5"}, lambda e: None, PROFILE, None, "https://x")
    assert "REAL MESSAGE" in key


def test_no_answer_leaves_the_full_message_as_voicemail():
    made, _ = run_escalation(public="https://x.trycloudflare.com")
    family = next(c for c in made if c["to"] == ENV["FAMILY_PHONE"])["twiml"]
    assert "Sandy may have fallen" in family.split("</Gather>")[-1]  # after both openings: the voicemail


def test_911_remembers_the_conversation_on_each_call():
    hist, seen = {}, []
    ask = lambda inc, q, past: seen.append(list(past)) or f"answer to {q}"  # noqa: E731
    for q in ("What happened?", "Where is that?"):
        handle("/twilio/psap", {"inc": "i"}, {"SpeechResult": q, "CallSid": "CA1"}, lambda e: None, PROFILE, ask,
               "https://x", hist)
    assert seen[1] == [("What happened?", "answer to What happened?")]
