"""Everything CareBot says out loud, as templates. Code picks the line; nothing is free-form.

These lines are the in-room half of the honesty table in PLAN.md ("What CareBot may say").
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Profile:
    resident_name: str = "Ruth"
    family_name: str = "Priya"

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
    """Said only after the sends succeed, naming only the channels that actually worked."""
    texted = "neighbors" in channels
    called = [x for x in (p.family_name if "family" in channels else None,
                          "911" if "911" in channels else None) if x]
    if not texted and not called:
        return "I couldn't reach anyone yet. I'm still trying."
    parts = []
    if texted:
        parts.append("texted your neighbors")
    if called:
        parts.append("called " + " and ".join(called))
    return "I'm getting help. I've " + " and ".join(parts) + "."


def helper_coming(name: str, street: str | None) -> str:
    return f"{name} from {street} is on the way." if street else f"{name} is on the way."


def still_getting_help() -> str:
    return "OK. I'm still getting help."


def passed_on(p: Profile) -> str:
    return f"Thank you. I'm passing that on to {p.family_name} and 911."


def psap_unanswered() -> str:
    return "I couldn't reach 911 yet. I'm trying again."
