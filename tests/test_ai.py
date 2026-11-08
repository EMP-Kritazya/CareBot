"""Claude in the middle: guardrails, fallbacks, understanding, comfort windows, 911 answers.
No network: a fake model returns canned replies (or fails)."""

import time

import pytest

from carebot.ai.dispatcher import IDK, answer, check_answer, offline_answer
from carebot.ai.llm import NoAI
from carebot.ai.phrasing import Phraser, check_line, fixed_lines
from carebot.ai.understand import Understander
from carebot.config import load_config
from carebot.core import state as st
from carebot.core.scenario import Scenario
from carebot.core.script import Profile
from carebot.escalate.facts import briefing, fact_sheet

NAMES = ("Sandy", "Suyog", "Krit")
PROFILE = load_config("config/mock.yaml")["profile"]


class FakeAI:
    enabled = True
    name = "fake"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = 0

    def generate(self, system, prompt, schema=None, **kw):
        self.calls += 1
        r = self.replies.pop(0) if self.replies else None
        if isinstance(r, Exception):
            raise r
        return r

    def typical_ms(self):
        return 10.0


# ---------------------------------------------------------------- the guardrail

CHECK_IN = "Sandy, I saw you fall. Are you OK? Say 'I'm OK' or 'help'."


def test_good_rewrite_passes():
    assert check_line(CHECK_IN, "Sandy, I saw you fall. Are you alright? Just say 'I'm OK' or 'help'.", NAMES)


@pytest.mark.parametrize("bad", [
    "I saw you fall. Are you OK? Say 'I'm OK' or 'help'.",                       # dropped his name
    "Sandy, I saw you fall. Are you alright?",                                    # dropped the cues
    "Sandy, I saw you fall. Help is on the way. Say 'I'm OK' or 'help'.",         # new promise
    "Sandy, I saw you fall. I've called 911. Say 'I'm OK' or 'help'.",            # new claim + name
    "Sandy, I saw you fall. Krit will be there in 5 minutes. Say 'I'm OK' or 'help'.",  # new name, ETA
    "Sandy, don't worry, you'll be fine. Say 'I'm OK' or 'help'.",                # false comfort
    "Sandy, stay still, you may be injured. Say 'I'm OK' or 'help'.",             # medical advice
    "",
])
def test_bad_rewrites_are_rejected(bad):
    assert not check_line(CHECK_IN, bad, NAMES)


def test_reply_may_echo_what_he_said_but_not_add_advice():
    template = "I hear you, Sandy. I've passed that on to Suyog and 911."
    ok = "I'm sorry your hip hurts, Sandy. I've passed that on to Suyog and 911."
    assert check_line(template, ok, NAMES, heard="my hip hurts")
    assert not check_line(template, ok + " Try not to move.", NAMES, heard="my hip hurts")


# ---------------------------------------------------------------- polish: fallbacks + cache

def test_polish_uses_a_good_rewrite_and_caches_it():
    ai = FakeAI({"line": "Sandy, I saw you fall. Are you alright? Say 'I'm OK' or 'help'."})
    ph = Phraser(ai, NAMES)
    line, used = ph.polish(CHECK_IN)
    assert used and line.startswith("Sandy, I saw you fall. Are you alright?")
    assert ph.polish(CHECK_IN) == (line, True) and ai.calls == 1  # cached


@pytest.mark.parametrize("reply", [None, TimeoutError("slow"), {"line": "Help is on the way, Sandy!"}, "not json"])
def test_polish_falls_back_to_the_template(reply):
    ph = Phraser(FakeAI(reply), NAMES)
    assert ph.polish(CHECK_IN) == (CHECK_IN, False)


def test_polish_off_without_ai():
    assert Phraser(NoAI(), NAMES).polish(CHECK_IN) == (CHECK_IN, False)


def test_every_fixed_line_passes_its_own_guardrail():
    for line in fixed_lines(Profile(), "Krit"):
        assert check_line(line, line, NAMES), line


# ---------------------------------------------------------------- understanding

def test_keyword_help_is_never_overridden():
    u = Understander(FakeAI({"intent": "ok", "concern": ""}))
    assert u.check_in("help me") == "help"


def test_symptoms_count_as_help_even_if_he_says_ok():
    u = Understander(FakeAI({"intent": "ok", "concern": ""}))
    assert u.check_in("I think so, just a bit dizzy") == "help"


def test_claude_understands_a_free_form_ok():
    u = Understander(FakeAI({"intent": "ok", "concern": ""}))
    assert u.check_in("yeah yeah, I just sat down too fast, all good") == "ok"
    assert u.last_source == "claude"


def test_claude_concern_means_help():
    u = Understander(FakeAI({"intent": "ok", "concern": "feels strange"}))
    assert u.check_in("I feel a little strange") == "help"


def test_understanding_falls_back_to_keywords():
    u = Understander(FakeAI(None))
    assert u.check_in("I'm okay") == "ok" and u.last_source == "keywords"


def test_report_never_downgrades_hospital():
    u = Understander(FakeAI({"status": "with_resident"}))
    assert u.report("I'm his neighbor, we're taking him to the hospital") == "hospital"


def test_report_from_claude():
    u = Understander(FakeAI({"status": "ambulance_here"}))
    assert u.report("the medics just walked in") == "ambulance_here"


# ---------------------------------------------------------------- comfort window (state machine)

def alerted():
    s = Scenario()
    s.fall()
    s.silence()
    s.silence()
    return s


def test_comfort_window_pauses_alarm_and_says_only_true_things():
    s = alerted()  # alert decided, but the sends haven't been confirmed yet
    s.send(st.ComfortWindow(s.t))
    ask = s.last(st.Ask)
    assert ask.kind == "comfort" and ask.answer == "intent"
    assert ask.text == "Sandy, I'm here with you. You can talk to me."  # no "help called" before sends
    assert isinstance(s.actions[-2], st.StopAlarm)


def test_comfort_window_mentions_krit_only_after_yes():
    s = alerted()
    s.sent()
    s.send(st.ComfortWindow(s.t))
    assert "I've called for help." in s.last(st.Ask).text
    s.silence(10)
    s.neighbor("Krit", "yes")
    s.send(st.ComfortWindow(s.t))
    assert "Krit is on the way." in s.last(st.Ask).text


def test_what_he_says_is_relayed_word_for_word_and_alarm_resumes():
    s = alerted()
    s.sent()
    s.send(st.ComfortWindow(s.t))
    s.send(st.Heard(s.last(st.Ask).ask_id, "help", "my hip really hurts", s.t))
    update = s.last(st.SendUpdate)
    assert (update.status, update.quote, update.source) == ("resident_said", "my hip really hurts", "resident")
    reply = s.last(st.Say)
    assert reply.kind == "comfort_reply" and reply.heard == "my hip really hurts"
    assert isinstance(s.actions[-1], st.StartAlarm) and s.mode == "ALERTED"


def test_im_ok_in_a_comfort_window_closes():
    s = alerted()
    s.sent()
    s.send(st.ComfortWindow(s.t))
    s.speak("I'm OK")
    assert s.mode == "MONITORING" and s.incident.outcome == "resident_ok"


def test_silence_in_a_comfort_window_resumes_the_alarm():
    s = alerted()
    s.sent()
    s.send(st.ComfortWindow(s.t))
    s.silence(10)
    assert isinstance(s.actions[-1], st.StartAlarm) and s.state.awaiting is None


def test_no_comfort_window_mid_question():
    s = alerted()
    s.sent()
    s.press("lying")  # asking "who's there"
    n = len(s.actions)
    s.send(st.ComfortWindow(s.t))
    assert len(s.actions) == n


def test_runtime_opens_comfort_windows_while_idle():
    from carebot.hw import make_hardware
    from carebot.runtime import Runtime

    class Voice:
        def __init__(self):
            self.asks = []

        def submit(self, a):
            self.asks.append(a)

    class Notifier:
        def escalate(self, *a): pass
        def pause(self): pass
        def resume(self): pass
        def update(self, u): pass

    clock = {"t": 1000.0}
    voice = Voice()
    rt = Runtime(st.Machine(), make_hardware("mock", {p: {"verbose": False} for p in ("pantilt", "eyes", "buzzer", "button")}),
                 voice, Notifier(), narrate=False, clock=lambda: clock["t"], comfort_every_s=30)
    rt.post(st.Detected("fall", "resident", clock["t"]))
    rt.pump()
    for _ in range(2):
        rt.post(st.Silence(voice.asks[-1].ask_id, clock["t"]))
        rt.pump()
    rt.pump()           # idle starts
    clock["t"] += 31
    rt.pump()           # posts the window
    rt.pump()           # handles it
    assert voice.asks[-1].kind == "comfort"


# ---------------------------------------------------------------- 911: fact sheet + answers

def facts_after_alert(quote=None):
    s = Scenario()
    s.t = time.time() - 300
    s.fall()
    s.silence()
    s.silence()
    s.sent()
    s.neighbor("Krit", "yes")
    if quote:
        s.send(st.ComfortWindow(s.t))
        s.send(st.Heard(s.last(st.Ask).ask_id, "help", quote, s.t))
    return fact_sheet(s.incident, PROFILE, time.time(), camera_now="lying down")


def test_fact_sheet_and_briefing_are_facts_only():
    f = facts_after_alert()
    assert f["resident"]["name"] == "Sandy" and f["helper"] == "Krit"
    assert sum("no answer" in c for c in f["check_ins"]) == 2
    b = briefing(f)
    assert "no answer to 2 spoken check-ins" in b and "Neighbor Krit has a key." in b
    for banned in ("breath", "conscious", "injur", "fine"):
        assert banned not in b.lower()


def test_quotes_reach_the_fact_sheet_verbatim():
    f = facts_after_alert(quote="my hip really hurts")
    assert f["quotes"][-1] == {"time": f["quotes"][-1]["time"], "who": "Sandy", "said": "my hip really hurts"}


@pytest.mark.parametrize("q", ["Is he breathing?", "Is he conscious?", "Is he injured?", "Is he OK?"])
def test_offline_condition_questions_say_i_dont_know(q):
    assert offline_answer(q, facts_after_alert()).startswith(IDK)


def test_offline_where_gives_address_and_key():
    a = offline_answer("Where is he?", facts_after_alert())
    assert "115th Street" in a and "Krit has a key" in a


def test_claude_answer_is_filtered_when_it_claims_his_condition():
    f = facts_after_alert()
    claim = FakeAI({"answer": "He is breathing but unconscious.", "from_facts": True})
    reply, source = answer("Is he breathing?", f, claim)
    assert source == "filtered" and reply.startswith(IDK) and "camera shows him" in reply  # facts, not a bare "I don't know"


def test_claude_may_repeat_his_own_words():
    f = facts_after_alert(quote="my hip really hurts")
    ok = FakeAI({"answer": "Sandy said: \"my hip really hurts\".", "from_facts": True})
    assert answer("Did he say anything?", f, ok)[1] == "claude"


def test_claude_failure_falls_back_to_offline():
    f = facts_after_alert()
    assert answer("Where is he?", f, FakeAI(None))[1] == "offline"


def test_numbers_not_in_the_facts_are_rejected():
    f = facts_after_alert()
    assert not check_answer("He will be there in 4 minutes.", f)


def test_filter_uses_whole_words():
    f = facts_after_alert()
    assert check_answer("His son and the neighbors were alerted.", f)
    assert not check_answer("He seems alert.", f)


def test_declining_to_predict_is_fine_but_predicting_is_not():
    f = facts_after_alert()
    assert check_answer("I don't know when anyone will arrive.", f)
    assert not check_answer("Krit will arrive soon.", f)


def test_offline_eta_question_never_gives_an_eta():
    a = offline_answer("How long until the neighbor arrives?", facts_after_alert())
    assert a.startswith("I don't know when anyone will arrive.") and "Krit" in a


# ---- from the live 911 test call

def test_whats_your_emergency_gets_the_summary():
    a = offline_answer("Hey, what's your emergency?", facts_after_alert())
    assert a.startswith("This is CareBot") and "115th Street" in a and "fall" in a


def test_call_screener_hears_who_is_calling():
    a = offline_answer("Hi, if you record your name and reason for calling, I'll see if this person is available.",
                       facts_after_alert())
    assert a.startswith("This is CareBot")


def test_position_question_reports_the_camera():
    a = offline_answer("Person still on the ground or is are they stood up?", facts_after_alert())
    assert "camera shows him lying down" in a


def test_condition_answer_adds_what_the_camera_sees():
    a = offline_answer("Can he move?", facts_after_alert())
    assert a.startswith(IDK) and "camera shows him lying down" in a


def test_who_is_coming_sounds_natural():
    a = offline_answer("Did anybody come?", facts_after_alert())
    assert "Krit" in a and "CareBot has contacted" in a



def test_conversation_history_reaches_claude():
    seen = {}

    class Recording(FakeAI):
        def generate(self, system, prompt, schema=None, **kw):
            seen["prompt"] = prompt
            return {"answer": "West 115th Street, apartment 4A.", "from_facts": True}

    answer("And where is that?", facts_after_alert(), Recording(), history=[("What happened?", "He fell.")])
    assert "DISPATCHER: What happened?\nCAREBOT: He fell." in seen["prompt"]


def test_pronouns_come_from_the_profile_never_guessed():
    f = facts_after_alert()
    assert f["resident"]["pronouns"] == "he/him"
    assert "camera shows him" in offline_answer("Is he breathing?", f)
    assert "He did not answer" in offline_answer("Why are you calling?", f)
    f["resident"]["pronouns"] = "she/her"
    assert "camera shows her" in offline_answer("Is she breathing?", f)
    assert "She did not answer" in offline_answer("Why are you calling?", f)



def test_admitting_not_knowing_passes_but_a_claim_does_not():
    """Live call: "Is he hurt?" -> Claude's honest "I don't know if he's hurt" was being blocked."""
    f = facts_after_alert()
    assert check_answer("I don't know if he's hurt; the camera shows him lying on the floor.", f)
    assert check_answer("I can't tell whether anything is broken.", f)
    assert not check_answer("He looks hurt.", f)
    assert not check_answer("I don't know, but he looks hurt.", f)
    assert not check_answer("He's breathing and I don't know about the rest.", f)



def test_cant_monitor_is_an_honest_admission():
    f = facts_after_alert()
    assert check_answer("I can't monitor his breathing; the camera shows him lying on the floor.", f)



def test_fact_sheet_names_the_family_contact_and_that_replies_dont_come_back():
    f = facts_after_alert()
    assert "Suyog" in f["family_contact"] and "son" in f["family_contact"]
    assert "don't come back" in f["family_contact"]



def test_comfort_talk_is_not_counted_as_a_check_in():
    s = Scenario()
    s.t = time.time() - 120
    s.fall()
    s.silence()
    s.silence()
    s.sent()
    s.send(st.ComfortWindow(s.t))
    s.silence()
    f = fact_sheet(s.incident, PROFILE, time.time())
    assert sum("no answer" in c for c in f["check_ins"]) == 2
    assert any("comfort" in c for c in f["other_prompts"])
    assert "no answer to 2 spoken check-ins" in briefing(f)
