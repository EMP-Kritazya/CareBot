"""Human-readable lines for state machine actions (terminal narration for simulate and watcher)."""

from __future__ import annotations

from carebot.core import state as st

DIM, BOLD, RED, GREEN, YELLOW, CYAN, RESET = "\033[2m", "\033[1m", "\033[31m", "\033[32m", "\033[33m", "\033[36m", "\033[0m"


def describe(a) -> str | None:
    if isinstance(a, st.Ask):
        how = "asks loudly" if a.loud else "asks"
        listen = "listens for a full answer" if a.listen == "transcript" else "listens for 'I'm OK' or 'help'"
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
        if a.family_only:
            return f"{YELLOW}{BOLD}TELL FAMILY ONLY ->{RESET} Suyog (he got back up but didn't answer; no 911, no neighbors)"
        who = "3 nearest neighbors + Suyog + 911" if a.include_neighbors else "Suyog + 911 (someone is already with him)"
        return f"{RED}{BOLD}ESCALATE ->{RESET} {who}"
    if isinstance(a, (st.PauseEscalation, st.ResumeEscalation)):
        return f"{DIM}escalation {'paused' if isinstance(a, st.PauseEscalation) else 'resumed'}{RESET}"
    if isinstance(a, st.SendUpdate) and a.source == "camera":
        return f"{YELLOW}UPDATE ->{RESET} [{a.status}] the camera saw him get back up (no 'I'm OK' yet)"
    if isinstance(a, st.SendUpdate) and a.source == "neighbor":
        return f"{YELLOW}UPDATE -> family + 911:{RESET} [{a.status}] {a.helper} replied YES and is on the way"
    if isinstance(a, st.SendUpdate) and a.source == "resident":
        return f'{YELLOW}UPDATE ->{RESET} [{a.status}] he said: "{a.quote}"'
    if isinstance(a, st.SendUpdate):
        return f'{YELLOW}UPDATE ->{RESET} [{a.status}] someone at the device said: "{a.quote}"'
    if isinstance(a, st.Publish):
        p = a.payload
        return f"{DIM}map <- {p['type']}" + (f" {p.get('status') or p.get('mode') or p.get('outcome') or p.get('event', '')}") + RESET
    if isinstance(a, st.RestartStillness):
        return (f"{DIM}stillness timer restarted" + (" + cooldown" if a.cooldown else "")
                + (", next stillness check waits longer" if a.backoff else "") + RESET)
    if isinstance(a, st.LogLocal):
        return f"{DIM}logged on the robot only: {a.kind} ({a.detail}). Nothing sent.{RESET}"
    return None
