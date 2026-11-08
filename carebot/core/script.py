"""Everything CareBot says out loud, as templates. Code picks the line; nothing is free-form.

These lines are the in-room half of the honesty table in PLAN.md ("What CareBot may say").
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Profile:
    resident_name: str = "Sandy"
    family_name: str = "Suyog"

    @classmethod
    def from_config(cls, cfg: dict) -> "Profile":
        p = cfg.get("profile", {})
        return cls(resident_name=p.get("resident_name", cls.resident_name),
                   family_name=p.get("family_name", cls.family_name))


OK_HELP = "Say 'I'm OK' or 'help'."


def check_in(p: Profile, event: str, who: str, n: int) -> str:
    if who != "resident":
        line = f"Is everyone OK? {OK_HELP}"
    elif event == "fall":
        line = f"{p.resident_name}, I saw you fall. Are you OK? {OK_HELP}"
    elif event == "stillness":
        line = f"{p.resident_name}, you haven't moved in a while. Are you OK? {OK_HELP}"
    else:
        line = f"{p.resident_name}, are you OK? {OK_HELP}"
    return line if n == 1 else f"I didn't hear you. {line}"


def got_up_check_in(p: Profile, who: str, n: int) -> str:
    """The camera saw them get back up during the check-in."""
    line = (f"{p.resident_name}, I see you got up." if who == "resident" else "I see you got up.")
    line += " Are you OK? Say 'I'm OK' or press my button."
    return line if n == 1 else f"I didn't hear you. {line}"


def got_up_alerted(p: Profile, who: str, helper: str | None, alerts_sent: bool) -> str:
    """The camera saw them get back up after the alert went out. Help is not called off."""
    line = f"{p.resident_name}, I see you're up." if who == "resident" else "I see you're up."
    line += " If you're OK, press my button and say 'I'm OK'."
    if helper:
        line += f" {helper} is still on the way."
    elif alerts_sent:
        line += " I've already called for help."
    return line


def told_family_got_up(p: Profile, who: str) -> str:
    """Said only after the family message was sent."""
    return f"I didn't hear an answer, so I've let {p.family_name} know about the fall."


def glad_ok(p: Profile, who: str) -> str:
    return f"Thanks, {p.resident_name}. I'm glad you're OK." if who == "resident" else "Thank you. I'm glad everyone is OK."


def confirm(p: Profile, who: str, n: int) -> str:
    if who == "resident":
        line = f"Thanks, {p.resident_name}. Please say 'I'm OK' so I know you're alright."
    else:
        line = "Thanks. Please say 'I'm OK' if everyone is alright."
    return line if n == 1 else f"I didn't hear you. {line}"


def onscene(p: Profile, who: str, resident_posture: str, n: int) -> str:
    if who == "resident" and resident_posture == "lying":
        seen = f"I see {p.resident_name} is still on the floor. "
    elif who == "resident" and resident_posture == "out_of_view":
        seen = f"I can't see {p.resident_name} right now. "
    else:
        seen = ""
    line = f"Hello. {seen}Who are you, and what's happening?"
    return line if n == 1 else f"Sorry, I didn't catch that. {line}"


def getting_help(p: Profile, channels: tuple[str, ...]) -> str:
    """Said only after the sends succeed, naming only who was actually reached. "Contacted" stays
    true whether a person got a call or a text."""
    who = [x for x, key in (("your neighbors", "neighbors"), (p.family_name, "family"), ("911", "911"))
           if key in channels]
    if not who:
        return "I couldn't reach anyone yet. I'm still trying."
    listed = who[0] if len(who) == 1 else ", ".join(who[:-1]) + " and " + who[-1]
    return f"I'm getting help. I've contacted {listed}."


def helper_coming(name: str, street: str | None) -> str:
    return f"{name} from {street} is on the way." if street else f"{name} is on the way."


def still_getting_help() -> str:
    return "I'm still getting help."


def comfort(p: Profile, who: str, helper: str | None, help_called: bool) -> str:
    """Comfort window while waiting. Only states what's true: who's coming if someone said yes,
    that help was called only if the sends went out."""
    line = f"{p.resident_name}, I'm here with you." if who == "resident" else "I'm here with you."
    if helper:
        line += f" {helper} is on the way."
    elif help_called:
        line += " I've called for help."
    return line + " You can talk to me."


def comfort_reply(p: Profile, who: str) -> str:
    name = f", {p.resident_name}" if who == "resident" else ""
    return f"I hear you{name}. I've passed that on to {p.family_name} and 911."


def passed_on(p: Profile) -> str:
    return f"Thank you. I'm passing that on to {p.family_name} and 911."


def psap_unanswered() -> str:
    return "I couldn't reach 911 yet. I'm trying again."
