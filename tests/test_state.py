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
    assert update.status == "she_is_ok" and update.source == "resident" and update.quote == "I'm okay"
    assert s.mode == "MONITORING"
    assert "Thanks, Ruth. I'm glad you're OK." in s.said


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
    s.speak("I'm her neighbor, I'm with her now")
    assert s.mode == "ALERTED"
    assert s.last(st.Escalate).include_neighbors is False
    assert s.last(st.SendUpdate).quote == "I'm her neighbor, I'm with her now"


def test_out_of_view_press_does_not_claim_she_is_on_the_floor():
    s = alerted()
    s.press("out_of_view")
    assert "I can't see Ruth right now." in s.last(st.Ask).text
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


def test_with_her_stops_alarm_and_stays_open_until_outcome():
    s = alerted()
    s.press("lying")
    s.speak("I'm her neighbor, I'm here with her")
    assert s.mode == "ALERTED"
    assert s.incident.neighbor == "with_her"
    s.sms("HOSP we're taking her to the hospital")
    assert s.mode == "MONITORING" and s.incident.outcome == "hospital"


# ---- honesty

def test_getting_help_names_only_channels_that_worked():
    s = Scenario()
    s.fall()
    s.silence()
    s.silence()
    s.sent(channels=("neighbors",))
    assert s.last(st.Say).text == "I'm getting help. I've texted your neighbors."
    s2 = Scenario()
    s2.fall()
    s2.silence()
    s2.silence()
    s2.sent(channels=())
    assert s2.last(st.Say).text == "I couldn't reach anyone yet. I'm still trying."


def test_only_first_yes_is_announced():
    s = alerted()
    s.neighbor("Daniel", "yes")
    s.neighbor("Ana", "yes")
    assert [t for t in s.said if "on the way" in t] == ["Daniel is on the way."]


def test_911_unanswered_is_said_out_loud():
    s = alerted()
    s.call("911", "unanswered")
    assert s.last(st.Say).text == "I couldn't reach 911 yet. I'm trying again."


def test_no_ask_line_contains_a_diagnosis_or_promise():
    s = alerted()
    s.neighbor("Daniel", "yes")
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
    s.sms("she's ok")
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
    assert s.last(st.Ask).text == "Ruth, are you OK? Say 'I'm OK' or 'help'."
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
    ("I'm her neighbor, we're taking her to the hospital now", "hospital"),
    ("the paramedics are here", "ambulance_here"),
    ("she's okay, she just slipped", "she_is_ok"),
    ("I'm her neighbor", "with_her"),
    ("uh what", "unclear"),
])
def test_report_status_by_keywords(text, status):
    assert report_status_by_keywords(text) == status
