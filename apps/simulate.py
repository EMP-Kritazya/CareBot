"""Play the user stories through the real state machine, narrated in the terminal.

    python -m apps.simulate            # all of them
    python -m apps.simulate --story 1  # just one

No hardware, no network. The same decision code the robot runs; actions are printed, not executed.
"""

from __future__ import annotations

import argparse
import datetime as dt

from carebot.config import load_config
from carebot.core import state as st
from carebot.core.scenario import Scenario
from carebot.core.script import Profile
from carebot.core.narrate import BOLD, DIM, RESET, describe



class Narrator:
    def __init__(self, start: str) -> None:
        h, m = map(int, start.split(":"))
        self.base = dt.datetime(2026, 11, 8, h, m)

    def clock(self, t: float) -> str:
        return (self.base + dt.timedelta(seconds=t)).strftime("%-I:%M:%S %p")

    def scene(self, s: Scenario, text: str) -> None:
        print(f"{DIM}{self.clock(s.t):>11}{RESET}  {BOLD}{text}{RESET}")

    def actions(self, t: float, actions: list) -> None:
        for a in actions:
            line = describe(a)
            if line:
                print(f"{DIM}{self.clock(t):>11}{RESET}    {line}")


def story_1(n: Narrator, s: Scenario) -> None:
    n.scene(s, "Sandy gets up in the night and falls by the bed. He stays down.")
    s.fall()
    s.wait(15)
    n.scene(s, "...silence.")
    s.silence(0)
    n.scene(s.wait(15), "...silence again.")
    s.silence(0)
    n.scene(s.wait(2), "Twilio confirms: the 3 nearest neighbors are being rung, Suyog is texted, 911 is called.")
    s.sent()
    n.scene(s.wait(35), 'Krit answers the call and presses 1: YES.')
    s.neighbor("Krit", "yes", street="115th Street")
    n.scene(s.wait(150), "Krit arrives and presses the button. Sandy is still lying on the floor.")
    s.press("lying")
    n.scene(s.wait(4), "Krit: \"I'm his neighbor, we're taking him to the hospital now.\"")
    s.speak("I'm his neighbor, we're taking him to the hospital now.")


def story_2(n: Narrator, s: Scenario) -> None:
    n.scene(s, "Sandy's grandson naps on the couch for 5 minutes. He isn't the resident: no stillness timer.")
    s.wait(300)
    n.scene(s, "Sandy sits down hard on the floor to pick something up.")
    s.fall()
    n.scene(s.wait(3), "Sandy presses the button.")
    s.press("sitting")
    n.scene(s.wait(2), 'Sandy: "I\'m OK."')
    s.speak("I'm OK")


def story_3(n: Narrator, s: Scenario) -> None:
    n.scene(s, "Sandy slumps over in his armchair and doesn't move for a minute.")
    s.stillness()
    n.scene(s.wait(4), 'Sandy, weakly: "help."')
    s.speak("help")
    n.scene(s.wait(2), "Twilio confirms all sends.")
    s.sent()


def story_4(n: Narrator, s: Scenario) -> None:
    n.scene(s, "The grandson trips over the rug and goes down.")
    s.fall(who="other")
    n.scene(s.wait(5), 'Grandson: "I\'m OK, I\'m OK."')
    s.speak("I'm OK, I'm OK")


def story_5(n: Narrator, s: Scenario) -> None:
    n.scene(s, "Sandy falls by the kitchen counter and stays down.")
    s.send(st.Detected("fall", "resident", s.t, track_id=1))
    n.scene(s.wait(6), "Sandy gets back up on his own (the camera sees him standing).")
    s.send(st.GotUp(s.t, track_id=1))
    n.scene(s.wait(15), "...he walks off to the bathroom without answering.")
    s.silence(0)
    n.scene(s.wait(15), "...still no answer.")
    s.silence(0)
    n.scene(s.wait(2), "Twilio confirms: Suyog's text was sent.")
    s.sent(channels=("family",))


def story_6(n: Narrator, s: Scenario) -> None:
    n.scene(s, "Sandy falls and doesn't answer. CareBot alerts everyone.")
    s.fall()
    s.silence(15)
    s.silence(15)
    s.sent()
    n.scene(s.wait(30), 'Krit replies "YES".')
    s.neighbor("Krit", "yes", street="115th Street")
    n.scene(s.wait(40), "Sandy manages to get up before Krit arrives.")
    s.send(st.GotUp(s.t))


STORIES = {
    1: ("The 2am fall, no answer", "2:07", story_1),
    2: ("Grandson visits, false alarm", "16:30", story_2),
    3: ("Slumped in the chair", "15:15", story_3),
    4: ("The visitor falls", "16:45", story_4),
    5: ("Sandy gets up on his own (during the check-in)", "10:20", story_5),
    6: ("Sandy gets up on his own (after the alert)", "2:40", story_6),
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--story", type=int, choices=sorted(STORIES))
    ap.add_argument("--config", default="config/mock.yaml")
    args = ap.parse_args()
    profile = Profile.from_config(load_config(args.config))
    for num in [args.story] if args.story else sorted(STORIES):
        title, start, fn = STORIES[num]
        print(f"\n{BOLD}== Story {num}: {title} =={RESET}")
        narrator = Narrator(start)
        scenario = Scenario(profile, on_actions=narrator.actions)
        fn(narrator, scenario)
        sent = len(scenario.outbound)
        print(f"{DIM}{'':>11}{RESET}  {BOLD}Result:{RESET} mode {scenario.mode}, "
              f"{sent} outbound message{'s' if sent != 1 else ''}, outcome {scenario.incident.outcome}")


if __name__ == "__main__":
    main()
