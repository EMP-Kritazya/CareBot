"""Play the four user stories through the real state machine, narrated in the terminal.

    python -m apps.simulate            # all four
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

DIM, BOLD, RED, GREEN, YELLOW, CYAN, RESET = "\033[2m", "\033[1m", "\033[31m", "\033[32m", "\033[33m", "\033[36m", "\033[0m"


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


def describe(a) -> str | None:
    if isinstance(a, st.Ask):
        how = "asks loudly" if a.loud else "asks"
        listen = "listens for a full answer" if a.listen == "transcript" else "listens 15 s"
        return f'{CYAN}CareBot {how}:{RESET} "{a.text}"  {DIM}({listen}){RESET}'
    if isinstance(a, st.Say):
        return f'{CYAN}CareBot says:{RESET} "{a.text}"'
    if isinstance(a, st.SetEyes):
        color = {"alerted": RED, "help_coming": GREEN, "checking": YELLOW}.get(a.state, CYAN)
        return f"{DIM}eyes ->{RESET} {color}{a.state}{RESET}"
    if isinstance(a, st.Chirp):
        return f"{DIM}buzzer: chirp{RESET}"
    if isinstance(a, st.StartAlarm):
        return f"{RED}buzzer: ALARM until the button is pressed{RESET}"
    if isinstance(a, st.StopAlarm):
        return None  # noisy; the stop is implied by what follows
    if isinstance(a, st.Escalate):
        who = "3 nearest neighbors + Priya + 911" if a.include_neighbors else "Priya + 911 (someone is already with her)"
        return f"{RED}{BOLD}ESCALATE ->{RESET} {who}"
    if isinstance(a, (st.PauseEscalation, st.ResumeEscalation)):
        return f"{DIM}escalation {'paused' if isinstance(a, st.PauseEscalation) else 'resumed'}{RESET}"
    if isinstance(a, st.SendUpdate):
        return f'{YELLOW}UPDATE to Priya + 911 ->{RESET} [{a.status}] someone at the device said: "{a.quote}"'
    if isinstance(a, st.Publish):
        p = a.payload
        return f"{DIM}map <- {p['type']}" + (f" {p.get('status') or p.get('mode') or p.get('outcome') or p.get('event', '')}") + RESET
    if isinstance(a, st.LogLocal):
        return f"{DIM}logged on the robot only: {a.kind} ({a.detail}). Nothing sent.{RESET}"
    return None


def story_1(n: Narrator, s: Scenario) -> None:
    n.scene(s, "Ruth gets up in the night and falls by the bed. She stays down.")
    s.fall()
    s.wait(15)
    n.scene(s, "...silence.")
    s.silence(0)
    n.scene(s.wait(15), "...silence again.")
    s.silence(0)
    n.scene(s.wait(2), "Twilio confirms: texts to 3 neighbors sent, Priya called, 911 called.")
    s.sent()
    n.scene(s.wait(35), 'Daniel replies "YES" to the text.')
    s.neighbor("Daniel", "yes", street="115th Street")
    n.scene(s.wait(150), "Daniel arrives and presses the button. Ruth is still lying on the floor.")
    s.press("lying")
    n.scene(s.wait(4), "Daniel: \"I'm her neighbor, we're taking her to the hospital now.\"")
    s.speak("I'm her neighbor, we're taking her to the hospital now.")


def story_2(n: Narrator, s: Scenario) -> None:
    n.scene(s, "Ruth's grandson naps on the couch for 5 minutes. He isn't the resident: no stillness timer.")
    s.wait(300)
    n.scene(s, "Ruth sits down hard on the floor to pick something up.")
    s.fall()
    n.scene(s.wait(3), "Ruth presses the button.")
    s.press("sitting")
    n.scene(s.wait(2), 'Ruth: "I\'m OK."')
    s.speak("I'm OK")


def story_3(n: Narrator, s: Scenario) -> None:
    n.scene(s, "Ruth slumps in her armchair and doesn't move for 30 seconds.")
    s.stillness()
    n.scene(s.wait(4), 'Ruth, weakly: "help."')
    s.speak("help")
    n.scene(s.wait(2), "Twilio confirms all sends.")
    s.sent()


def story_4(n: Narrator, s: Scenario) -> None:
    n.scene(s, "The grandson trips over the rug and goes down.")
    s.fall(who="other")
    n.scene(s.wait(5), 'Grandson: "I\'m OK, I\'m OK."')
    s.speak("I'm OK, I'm OK")


STORIES = {
    1: ("The 2am fall, no answer", "2:07", story_1),
    2: ("Grandson visits, false alarm", "16:30", story_2),
    3: ("Slumped in the chair", "15:15", story_3),
    4: ("The visitor falls", "16:45", story_4),
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
