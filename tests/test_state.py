"""Transition-level rules: the button, honesty, stale answers, reports, reset."""

import pytest

from carebot.core import state as st
from carebot.core.scenario import Scenario
from carebot.voice.keywords import check_in_intent, report_status_by_keywords


def alerted() -> Scenario:
    s = Scenario()
    s.fall()
    s.silence()
    s.silence()
    s.sent()
    return s


# ---- button attribution

@pytest.mark.parametrize("posture,who", [("upright", "resident"), ("sitting", "resident"),
                                         ("lying", "other"), ("out_of_view", "other")])
def test_attribute_press(posture, who):
    assert st.attribute_press(posture) == who


def test_button_alone_never_closes_an_alert():
    s = alerted()
    s.press("upright")
    assert s.mode == "ALERTED"
    assert s.last(st.PauseEscalation)
    s.silence()
    s.silence()
    assert s.mode == "ALERTED"
    assert s.last(st.ResumeEscalation) and s.of(st.StartAlarm)
    assert any(e.detail == "button pressed, no spoken response" for e in s.incident.timeline)


def test_button_during_check_in_then_silence_escalates_with_note():
    s = Scenario()
    s.fall()
    s.press("upright")
    s.silence()
    s.silence()
    assert s.mode == "ALERTED"
    assert any(e.detail == "button pressed, no spoken response" for e in s.incident.timeline)


def test_resident_confirms_ok_after_alert_closes_and_tells_everyone():
    s = alerted()
    s.press("upright")
    s.speak("I'm okay")
    update = s.last(st.SendUpdate)
    assert update.status == "resident_ok" and update.source == "resident" and update.quote == "I'm okay"
    assert s.mode == "MONITORING"
    assert "Thanks, Sandy. I'm glad you're OK." in s.said


def test_resident_says_help_after_button_resumes_alarm():
    s = alerted()
    s.press("upright")
    s.speak("no, help me")
    assert s.mode == "ALERTED"
    assert s.last(st.ResumeEscalation)


def test_someone_else_during_check_in_escalates_without_neighbors_and_quotes_them():
    s = Scenario()
    s.fall()
    s.press("lying")
    s.speak("I'm his neighbor, I'm with him now")
    assert s.mode == "ALERTED"
    assert s.last(st.Escalate).include_neighbors is False
    assert s.last(st.SendUpdate).quote == "I'm his neighbor, I'm with him now"


def test_out_of_view_press_does_not_claim_she_is_on_the_floor():
    s = alerted()
    s.press("out_of_view")
    assert "I can't see Sandy right now." in s.last(st.Ask).text
    assert "floor" not in s.last(st.Ask).text


def test_unclear_onscene_reply_is_asked_once_more_then_passed_on_as_is():
    s = alerted()
    s.press("lying")
    s.speak("uh, what?")
    assert s.last(st.Ask).text.startswith("Sorry, I didn't catch that.")
    s.speak("mumble mumble")
    update = s.last(st.SendUpdate)
    assert update.status == "unclear" and update.quote == "mumble mumble"
    assert s.mode == "ALERTED"


def test_with_resident_stops_alarm_and_stays_open_until_outcome():
    s = alerted()
    s.press("lying")
    s.speak("I'm his neighbor, I'm here with him")
    assert s.mode == "ALERTED"
    assert s.incident.neighbor == "with_resident"
    s.sms("HOSP we're taking him to the hospital")
    assert s.mode == "MONITORING" and s.incident.outcome == "hospital"


# ---- honesty

def test_getting_help_names_only_channels_that_worked():
    s = Scenario()
    s.fall()
    s.silence()
    s.silence()
    s.sent(channels=("neighbors",))
    assert s.last(st.Say).text == "I'm getting help. I've contacted your neighbors."
    s2 = Scenario()
    s2.fall()
    s2.silence()
    s2.silence()
    s2.sent(channels=())
    assert s2.last(st.Say).text == "I couldn't reach anyone yet. I'm still trying."


def test_only_first_yes_is_announced():
    s = alerted()
    s.neighbor("Krit", "yes")
    s.neighbor("Ana", "yes")
    assert [t for t in s.said if "on the way" in t] == ["Krit is on the way."]


def test_911_unanswered_is_said_out_loud():
    s = alerted()
    s.call("911", "unanswered")
    assert s.last(st.Say).text == "I couldn't reach 911 yet. I'm trying again."


def test_no_ask_line_contains_a_diagnosis_or_promise():
    s = alerted()
    s.neighbor("Krit", "yes")
    for line in s.said:
        low = line.lower()
        for banned in ("help is on the way", "minutes", "you'll be fine", "injur", "breathing"):
            assert banned not in low, line


# ---- robustness

def test_stale_answer_to_an_old_question_is_ignored():
    s = Scenario()
    s.fall()
    old = s.last(st.Ask).ask_id
    s.silence()
    s.send(st.Heard(old, "ok", "I'm OK", s.t))  # late answer to ask #1
    assert s.mode == "CHECK_IN"


def test_detections_during_an_incident_are_ignored():
    s = Scenario()
    s.fall()
    n = len(s.actions)
    s.fall()
    s.stillness()
    assert len(s.actions) == n


def test_button_and_sms_while_monitoring_do_nothing():
    s = Scenario()
    s.press("upright")
    s.sms("he's ok")
    assert s.mode == "MONITORING" and not s.actions


def test_reset_from_map_closes_any_incident():
    s = alerted()
    s.reset()
    assert s.mode == "MONITORING" and s.incident.outcome == "reset"
    assert s.last(st.Publish).payload["type"] == "close"


def test_manual_trigger_runs_the_same_pipeline():
    s = Scenario()
    s.trigger()
    assert s.incident.event == "manual"
    assert s.last(st.Ask).text == "Sandy, are you OK? Say 'I'm OK' or 'help'."
    s.silence()
    s.silence()
    assert s.mode == "ALERTED"


# ---- keyword rules

@pytest.mark.parametrize("text,intent", [
    ("I'm OK", "ok"), ("i am fine", "ok"), ("okay", "ok"), ("yes I'm alright", "ok"),
    ("help", "help"), ("I'm not ok", "help"), ("I can't get up", "help"),
    ("I'm OK... no, help", "help"), ("", "unclear"), ("what time is it", "unclear"),
])
def test_check_in_intent(text, intent):
    assert check_in_intent(text) == intent


@pytest.mark.parametrize("text,status", [
    ("I'm his neighbor, we're taking him to the hospital now", "hospital"),
    ("the paramedics are here", "ambulance_here"),
    ("she's okay, she just slipped", "resident_ok"),
    ("I'm his neighbor", "with_resident"),
    ("uh what", "unclear"),
])
def test_report_status_by_keywords(text, status):
    assert report_status_by_keywords(text) == status



# ---- getting up on his own

def test_got_up_during_check_in_rewords_the_question():
    s = Scenario()
    s.fall()
    s.send(st.GotUp(s.t))
    ask = s.last(st.Ask)
    assert ask.text == "Sandy, I see you got up. Are you OK? Say 'I'm OK' or press my button."
    assert not ask.loud  # a fresh round of asks
    s.speak("I'm OK")
    assert s.mode == "MONITORING" and not s.outbound


def test_got_up_then_silence_tells_only_the_family():
    s = Scenario()
    s.fall()
    s.send(st.GotUp(s.t))
    s.silence()
    s.silence()
    esc = s.last(st.Escalate)
    assert esc.family_only and not esc.include_neighbors
    assert s.mode == "MONITORING" and s.incident.outcome == "got_up_no_answer"
    assert not s.of(st.StartAlarm), "no alarm for someone who walked away"


def test_got_up_then_help_is_still_a_full_alert():
    s = Scenario()
    s.fall()
    s.send(st.GotUp(s.t))
    s.speak("help")
    esc = s.last(st.Escalate)
    assert s.mode == "ALERTED" and esc.include_neighbors and not esc.family_only


def test_got_up_after_alert_is_passed_on_but_help_is_not_called_off():
    s = alerted()
    s.neighbor("Krit", "yes", street="115th Street")
    s.send(st.GotUp(s.t))
    assert s.last(st.Say).text == ("Sandy, I see you're up. If you're OK, press my button and say 'I'm OK'. "
                                   "Krit is still on the way.")
    update = s.last(st.SendUpdate)
    assert update.status == "got_up" and update.source == "camera"
    assert s.mode == "ALERTED"
    assert not any(isinstance(a, st.StopAlarm) for a in s.actions[-3:]), "keeps beeping until he confirms"


def test_got_up_after_alert_without_a_yes_says_help_was_called():
    s = alerted()
    s.send(st.GotUp(s.t))
    assert s.last(st.Say).text.endswith("I've already called for help.")


def test_got_up_is_reported_only_once_and_only_for_the_person_who_fell():
    s = Scenario()
    s.send(st.Detected("fall", "resident", s.t, track_id=4))
    s.send(st.GotUp(s.t, track_id=9))  # someone else standing up
    assert not any("got up" in t for t in s.said)
    s.send(st.GotUp(s.t, track_id=4))
    n = len(s.actions)
    s.send(st.GotUp(s.t, track_id=4))
    assert len(s.actions) == n


def test_got_up_after_button_does_not_interrupt_the_confirmation():
    s = Scenario()
    s.fall()
    s.press("upright")
    confirm = s.last(st.Ask)
    s.send(st.GotUp(s.t))
    assert s.last(st.Ask) == confirm


# ---- adaptive stillness: every close restarts the timer

def test_every_close_restarts_stillness_with_cooldown():
    for setup in ("ok", "hospital", "reset"):
        s = Scenario()
        s.fall()
        if setup == "ok":
            s.speak("I'm OK")
        elif setup == "hospital":
            s.silence(); s.silence(); s.sent(); s.press("lying")
            s.speak("I'm his neighbor, we're taking him to the hospital")
        else:
            s.reset()
        r = s.last(st.RestartStillness)
        assert r is not None and r.cooldown, setup
        assert not r.backoff, f"falls never back off the stillness timer ({setup})"


def test_ok_to_a_stillness_check_backs_off_the_next_one():
    s = Scenario()
    s.stillness()
    s.speak("I'm fine")
    assert s.last(st.RestartStillness) == st.RestartStillness(cooldown=True, backoff=True)


def test_stillness_backs_off_only_when_he_ends_up_saying_ok():
    s = Scenario()
    s.stillness()
    s.speak("help")
    s.sent()
    s.press("upright")
    s.speak("I'm OK")
    assert s.last(st.RestartStillness).backoff is True  # he did end up saying he's OK
    s2 = Scenario()
    s2.stillness()
    s2.speak("help")
    s2.reset()
    assert s2.last(st.RestartStillness).backoff is False



def test_map_hears_who_was_actually_reached_and_call_results():
    s = Scenario()
    s.fall()
    s.silence()
    s.silence()
    s.sent(("family", "911"))
    pub = s.last(st.Publish).payload
    assert pub["type"] == "sent" and pub["channels"] == ["family", "911"]
    s.call("911", "answered")
    pub = s.last(st.Publish).payload
    assert pub == {"type": "call", "incident_id": s.incident.id, "target": "911", "status": "answered", "ts": s.t}



def test_neighbor_yes_updates_the_family_and_911_with_that_fact():
    s = Scenario()
    s.fall()
    s.silence()
    s.silence()
    s.sent()
    s.neighbor("Krit", "yes", street="115th Street")
    u = s.last(st.SendUpdate)
    assert (u.status, u.source, u.helper, u.street) == ("on_the_way", "neighbor", "Krit", "115th Street")
    from carebot.escalate.notifier import update_text
    text = update_text({"resident_name": "Sandy"}, u.status, u.quote, u.source, 0, u.helper, u.street)
    assert "Krit" in text and "replied YES" in text and "minute" not in text  # a fact, no ETA promised
    s.neighbor("Ana", "yes")                     # a second YES changes nothing
    assert len(s.of(st.SendUpdate)) == 1
