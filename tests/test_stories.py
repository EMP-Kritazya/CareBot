"""The four user stories from PLAN.md, end to end through the real state machine and keyword rules."""

from carebot.core import state as st
from carebot.core.scenario import Scenario


def test_story_1_the_2am_fall_no_answer():
    s = Scenario()
    s.fall()
    assert s.mode == "CHECK_IN"
    assert s.last(st.Ask).text == "Ruth, I saw you fall. Are you OK? Say 'I'm OK' or 'help'."
    assert s.last(st.SetEyes).state == "checking"
    assert s.of(st.Chirp)

    s.silence()
    second = s.last(st.Ask)
    assert second.loud and second.text.startswith("I didn't hear you.")
    assert not s.outbound, "nothing leaves the house before the second ask times out"

    s.silence()
    assert s.mode == "ALERTED"
    assert s.last(st.Escalate) == st.Escalate(s.incident.id, include_neighbors=True)
    assert s.last(st.SetEyes).state == "alerted"
    assert s.of(st.StartAlarm)
    assert not any("help" in t.lower() and "getting" in t.lower() for t in s.said), \
        "Ruth hears 'getting help' only after the sends succeed"

    s.sent()
    assert s.last(st.Say).text == "I'm getting help. I've texted your neighbors and called Priya and 911."

    s.wait(40).neighbor("Daniel", "yes", street="115th Street")
    assert s.last(st.Say).text == "Daniel from 115th Street is on the way."
    assert s.last(st.SetEyes).state == "help_coming"

    s.wait(120).press(resident_posture="lying")  # Daniel arrives; Ruth is still on the floor
    assert s.of(st.StopAlarm)
    ask = s.last(st.Ask)
    assert ask.listen == "transcript"
    assert ask.text == "Hello. I see Ruth is still on the floor. Who are you, and what's happening?"

    s.speak("I'm her neighbor, we're taking her to the hospital now.")
    update = s.last(st.SendUpdate)
    assert update.status == "hospital"
    assert update.quote == "I'm her neighbor, we're taking her to the hospital now."  # verbatim
    assert s.mode == "MONITORING"
    assert s.incident.outcome == "hospital"
    assert s.last(st.Publish).payload["type"] == "close"


def test_story_2_grandson_visits_false_alarm():
    s = Scenario()
    # The grandson naps on the couch: vision never emits Detected for a non-resident's stillness,
    # so the machine sees nothing. (Detector-level test lives with the detectors.)
    s.wait(300)
    assert s.mode == "MONITORING" and not s.actions

    s.fall()  # Ruth sits down hard on the floor
    assert s.last(st.Ask).text.startswith("Ruth, I saw you fall.")
    s.wait(3).press(resident_posture="sitting")
    assert s.last(st.Ask).text == "Thanks, Ruth. Please say 'I'm OK' so I know you're alright."
    s.speak("I'm OK")

    assert s.mode == "MONITORING"
    assert not s.outbound, "a false alarm sends nothing"
    log = s.last(st.LogLocal)
    assert log.kind == "check_in_ok" and "button+voice" in log.detail
    assert s.last(st.SetEyes).state == "watching"


def test_story_3_slumped_in_the_chair_says_help():
    s = Scenario()
    s.stillness()
    assert s.last(st.Ask).text == "Ruth, you haven't moved in a while. Are you OK? Say 'I'm OK' or 'help'."
    s.wait(4).speak("help")
    assert s.mode == "ALERTED"
    assert len(s.of(st.Ask)) == 1, "'help' escalates at once, with no second ask"
    assert s.incident.event == "stillness"
    assert any(e.kind == "response" and e.detail.startswith("help") for e in s.incident.timeline)


def test_story_4_the_visitor_falls():
    s = Scenario()
    s.fall(who="other")
    s.silence()
    s.silence()
    s.sent()
    s.press(resident_posture="upright")  # Ruth presses it for her grandson
    s.speak("I'm OK")
    for line in s.said:
        assert "Ruth" not in line, f"resident's name used for a visitor's fall: {line!r}"
    assert s.last(st.Ask).text == "Thanks. Please say 'I'm OK' if everyone is alright."
    assert s.mode == "MONITORING"
