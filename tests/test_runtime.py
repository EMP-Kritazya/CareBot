"""The runtime wiring: events in, the state machine decides, actions reach hardware/voice/notifier."""

import threading
import time

from carebot.core import state as st
from carebot.hw import make_hardware
from carebot.runtime import BUTTON, Runtime
from carebot.voice.listen import Listener
from carebot.voice.worker import VoiceWorker

QUIET = {part: {"verbose": False} for part in ("pantilt", "eyes", "buzzer", "button")}


class FakeVoice:
    def __init__(self):
        self.actions = []

    def submit(self, a):
        self.actions.append(a)

    @property
    def last_ask(self):
        return [a for a in self.actions if isinstance(a, st.Ask)][-1]


class FakeNotifier:
    def __init__(self):
        self.calls = []

    def escalate(self, incident, include_neighbors, family_only=False):
        self.calls.append(("escalate", incident.id, include_neighbors, family_only))

    def pause(self):
        self.calls.append(("pause",))

    def resume(self):
        self.calls.append(("resume",))

    def update(self, u):
        self.calls.append(("update", u.status, u.quote))


def make(posture=("upright",)):
    hw = make_hardware("mock", QUIET)
    voice, notifier = FakeVoice(), FakeNotifier()
    published = []
    rt = Runtime(st.Machine(), hw, voice, notifier, publish=published.append, posture=lambda: posture[0],
                 narrate=False)
    return rt, hw, voice, notifier, published


def test_story_1_through_the_runtime():
    posture = ["lying"]
    rt, hw, voice, notifier, published = make(posture)
    rt.post(st.Detected("fall", "resident", time.time()))
    rt.pump()
    assert hw.eyes.state == "checking" and hw.buzzer.chirps == 1
    assert voice.last_ask.text.startswith("Sandy, I saw you fall")

    for _ in range(2):
        rt.post(st.Silence(voice.last_ask.ask_id, time.time()))
        rt.pump()
    assert rt.state.mode == "ALERTED" and hw.buzzer.alarm_on and hw.eyes.state == "alerted"
    assert notifier.calls[-1][0] == "escalate" and notifier.calls[-1][2] is True
    assert published[-1]["type"] == "alert"

    rt.post(st.EscalationSent(("neighbors", "family", "911"), time.time()))
    rt.post(st.ReceiverReply("krit", "Krit", "115th Street", "yes", time.time()))
    rt.pump()
    assert hw.eyes.state == "help_coming"
    assert rt.last_said == "Krit from 115th Street is on the way."

    rt.hw.button.press()  # the real button callback path (mock driver)
    rt.pump()
    assert not hw.buzzer.alarm_on
    assert voice.last_ask.listen == "transcript"
    rt.post(st.Reported("hospital", "we're taking him to the hospital", "in_room", time.time(), voice.last_ask.ask_id))
    rt.pump()
    assert ("update", "hospital", "we're taking him to the hospital") in notifier.calls
    assert rt.state.mode == "MONITORING" and hw.eyes.state == "watching"


def test_button_uses_the_cameras_view_at_press_time():
    posture = ["upright"]
    rt, hw, voice, *_ = make(posture)
    rt.post(st.Detected("fall", "resident", time.time()))
    rt.post(BUTTON)
    rt.pump()
    assert voice.last_ask.text.startswith("Thanks, Sandy.")  # Sandy pressed it himself


def test_status_line():
    rt, *_ = make()
    assert rt.status() == "CareBot: watching"
    rt.post(st.ManualTrigger(time.time()))
    rt.pump()
    assert rt.status().startswith("CareBot: CHECK-IN")


# ---------------------------------------------------------------- voice worker

class FakeTTS:
    def __init__(self):
        self.said = []

    def say(self, text, loud=False):
        self.said.append(text)


def test_voice_worker_speaks_then_posts_the_typed_answer():
    posted = []
    listener = Listener(mic=None, settle_s=0)
    worker = VoiceWorker(FakeTTS(), listener, posted.append, listen_s=2)
    worker.start()
    worker.submit(st.Ask(1, "Sandy, are you OK?", "keywords"))
    deadline = time.time() + 2
    while not listener.listening.is_set() and time.time() < deadline:
        time.sleep(0.01)
    assert listener.type("I'm OK")
    while not posted and time.time() < deadline:
        time.sleep(0.01)
    worker.stop()
    assert isinstance(posted[0], st.Heard) and posted[0].intent == "ok" and posted[0].ask_id == 1


def test_voice_worker_posts_silence_after_timeout():
    posted = []
    worker = VoiceWorker(FakeTTS(), Listener(mic=None, settle_s=0), posted.append, listen_s=0.2)
    worker.start()
    worker.submit(st.Ask(7, "Are you OK?", "keywords"))
    deadline = time.time() + 2
    while not posted and time.time() < deadline:
        time.sleep(0.01)
    worker.stop()
    assert isinstance(posted[0], st.Silence) and posted[0].ask_id == 7


def test_newer_question_supersedes_the_old_one():
    posted = []
    tts = FakeTTS()
    listener = Listener(mic=None, settle_s=0)
    worker = VoiceWorker(tts, listener, posted.append, listen_s=5)
    worker.start()
    worker.submit(st.Ask(1, "first question", "keywords"))
    time.sleep(0.1)  # first question is now listening
    worker.submit(st.Ask(2, "Who are you?", "transcript"))
    deadline = time.time() + 3
    while "Who are you?" not in tts.said and time.time() < deadline:
        time.sleep(0.01)
    while not listener.listening.is_set() and time.time() < deadline:
        time.sleep(0.01)
    listener.type("I'm his neighbor, we're taking him to the hospital")
    while not posted and time.time() < deadline:
        time.sleep(0.01)
    worker.stop()
    assert len(posted) == 1
    assert isinstance(posted[0], st.Reported) and posted[0].ask_id == 2 and posted[0].status == "hospital"


def test_typing_when_nobody_is_listening_is_refused():
    assert Listener(mic=None).type("hello") is False


def test_family_only_alert_reaches_the_notifier_after_the_incident_closed():
    rt, hw, voice, notifier, _ = make()
    rt.post(st.Detected("fall", "resident", time.time(), track_id=3))
    rt.pump()
    rt.post(st.GotUp(time.time(), track_id=3))
    rt.pump()
    for _ in range(2):
        rt.post(st.Silence(voice.last_ask.ask_id, time.time()))
        rt.pump()
    assert notifier.calls[-1][2:] == (False, True)  # no neighbors, family only
    assert rt.state.mode == "MONITORING" and not hw.buzzer.alarm_on
    rt.post(st.EscalationSent(("family",), time.time()))
    rt.pump()
    assert rt.last_said == "I didn't hear an answer, so I've let Suyog know about the fall."


def test_restart_stillness_reaches_vision():
    calls = []
    hw = make_hardware("mock", QUIET)
    rt = Runtime(st.Machine(), hw, FakeVoice(), FakeNotifier(), narrate=False,
                 restart_stillness=lambda cooldown, backoff: calls.append((cooldown, backoff)))
    rt.post(st.Detected("stillness", "resident", time.time()))
    rt.pump()
    rt.post(st.Heard(rt.state.last_ask_id, "ok", "I'm OK", time.time()))
    rt.pump()
    assert calls == [(True, True)]


def test_reported_bug_no_stillness_check_right_after_im_ok():
    """Live report: fall, no answer, alert, button + "I'm OK"... then an immediate stillness check.
    Now the close restarts vision's stillness timer with a cooldown, so nothing fires."""
    import copy

    from carebot.config import load_config
    from carebot.vision.detectors import Detectors
    from carebot.vision.synth import frame, person

    cfg = copy.deepcopy(load_config("config/mock.yaml")["detectors"])
    cfg["stillness"]["profile"] = "demo"
    cfg["stillness"]["timers"]["demo"] = {"sitting": 30, "upright": 20, "slumped": 10}
    cfg["stillness"]["cooldown_s"]["demo"] = 60
    det = Detectors(cfg)
    t = {"now": 0.0}
    hw = make_hardware("mock", QUIET)
    voice = FakeVoice()
    rt = Runtime(st.Machine(), hw, voice, FakeNotifier(), narrate=False,
                 restart_stillness=lambda c, b: det.stillness.restart(t["now"], c, b))

    def still(seconds):  # Sandy sits still in his chair, watched by the real detector
        events = []
        for _ in range(int(seconds)):
            events += det.update(frame(t["now"], person(1, (0.5, 0.6), "sitting"), resident=1))
            t["now"] += 1
        return [e for e in events if e.kind == "stillness"]

    # During the incident he sits there, pressing the button and talking: 40 s of "stillness".
    rt.post(st.Detected("fall", "resident", time.time()))
    rt.pump()
    for _ in range(2):
        rt.post(st.Silence(voice.last_ask.ask_id, time.time()))
        rt.pump()
    still(40)
    rt.post(BUTTON)
    rt.pump()
    rt.post(st.Heard(voice.last_ask.ask_id, "ok", "I'm OK", time.time()))
    rt.pump()
    assert rt.state.mode == "MONITORING"
    assert still(55) == [], "no stillness check right after he said he's OK"
    assert still(60) != [], "after the cooldown the normal timer applies again"
