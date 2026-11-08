"""The user stories from PLAN.md, end to end through the real state machine and keyword rules."""

from carebot.core import state as st
from carebot.core.scenario import Scenario


def test_story_1_the_2am_fall_no_answer():
    s = Scenario()
    s.fall()
    assert s.mode == "CHECK_IN"
    assert s.last(st.Ask).text == "Sandy, I saw you fall. Are you OK? Say 'I'm OK' or 'help'."
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
        "Sandy hears 'getting help' only after the sends succeed"

    s.sent()
    assert s.last(st.Say).text == "I'm getting help. I've contacted your neighbors, Suyog and 911."

    s.wait(40).neighbor("Krit", "yes", street="115th Street")
    assert s.last(st.Say).text == "Krit from 115th Street is on the way."
    assert s.last(st.SetEyes).state == "help_coming"

    s.wait(120).press(resident_posture="lying")  # Krit arrives; Sandy is still on the floor
    assert s.of(st.StopAlarm)
    ask = s.last(st.Ask)
    assert ask.listen == "transcript"
    assert ask.text == "Hello. I see Sandy is still on the floor. Who are you, and what's happening?"

    s.speak("I'm his neighbor, we're taking him to the hospital now.")
    update = s.last(st.SendUpdate)
    assert update.status == "hospital"
    assert update.quote == "I'm his neighbor, we're taking him to the hospital now."  # verbatim
    assert s.mode == "MONITORING"
    assert s.incident.outcome == "hospital"
    assert s.last(st.Publish).payload["type"] == "close"


def test_story_2_grandson_visits_false_alarm():
    s = Scenario()
    # The grandson naps on the couch: vision never emits Detected for a non-resident's stillness,
    # so the machine sees nothing. (Detector-level test lives with the detectors.)
    s.wait(300)
    assert s.mode == "MONITORING" and not s.actions

    s.fall()  # Sandy sits down hard on the floor
    assert s.last(st.Ask).text.startswith("Sandy, I saw you fall.")
    s.wait(3).press(resident_posture="sitting")
    assert s.last(st.Ask).text == "Thanks, Sandy. Please say 'I'm OK' so I know you're alright."
    s.speak("I'm OK")

    assert s.mode == "MONITORING"
    assert not s.outbound, "a false alarm sends nothing"
    log = s.last(st.LogLocal)
    assert log.kind == "check_in_ok" and "button+voice" in log.detail
    assert s.last(st.SetEyes).state == "watching"


def test_story_3_slumped_in_the_chair_says_help():
    s = Scenario()
    s.stillness()
    assert s.last(st.Ask).text == "Sandy, you haven't moved in a while. Are you OK? Say 'I'm OK' or 'help'."
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
    s.press(resident_posture="upright")  # Sandy presses it for his grandson
    s.speak("I'm OK")
    for line in s.said:
        assert "Sandy" not in line, f"resident's name used for a visitor's fall: {line!r}"
    assert s.last(st.Ask).text == "Thanks. Please say 'I'm OK' if everyone is alright."
    assert s.mode == "MONITORING"



def test_story_5_sandy_gets_up_on_his_own():
    """He falls, gets back up during the check-in, and walks off without answering."""
    s = Scenario()
    s.send(st.Detected("fall", "resident", s.t, track_id=1))
    assert s.last(st.Ask).text.startswith("Sandy, I saw you fall.")
    s.wait(6).send(st.GotUp(s.t, track_id=1))
    assert s.last(st.Ask).text.startswith("Sandy, I see you got up.")
    s.silence()
    s.silence()
    # Only Suyog is told, with what the camera saw. No 911, no neighbors, no alarm.
    esc = s.last(st.Escalate)
    assert esc.family_only and not esc.include_neighbors
    assert s.mode == "MONITORING"
    s.sent(channels=("family",))
    assert s.last(st.Say).text == "I didn't hear an answer, so I've let Suyog know about the fall."
