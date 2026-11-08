"""Which speaker and microphone CareBot is using, so a wrong default (e.g. AirPods) is obvious."""

from __future__ import annotations

HEADPHONE_WORDS = ("airpods", "headphone", "headset", "buds", "beats")


def describe_audio(output_device: str | None, input_device: str | None) -> list[str]:
    """Human-readable lines about the active audio devices, with a warning for headphones."""
    try:
        import sounddevice as sd
    except Exception:  # noqa: BLE001
        return []
    lines = []
    try:
        out = output_device or sd.query_devices(kind="output")["name"]
        mic = sd.query_devices(input_device, kind="input")["name"] if input_device else sd.query_devices(kind="input")["name"]
    except Exception as e:  # noqa: BLE001
        return [f"Audio devices: couldn't read them ({e})"]
    lines.append(f"Speaking through: {out}{'' if output_device else ' (system default)'}")
    lines.append(f"Listening on:     {mic}{'' if input_device else ' (system default)'}")
    for kind, name in (("speaker", out), ("microphone", mic)):
        if any(w in name.lower() for w in HEADPHONE_WORDS):
            lines.append(f"WARNING: the {kind} is headphones ({name}). A robot in a room needs the room's "
                         f"{kind}: run with --speaker \"MacBook Pro Speakers\" --mic \"MacBook Pro Microphone\", "
                         f"or switch it in System Settings > Sound.")
    return lines


def list_audio() -> None:
    import sounddevice as sd

    for i, d in enumerate(sd.query_devices()):
        kinds = [k for k, n in (("mic", d["max_input_channels"]), ("speaker", d["max_output_channels"])) if n]
        print(f"  {i:>2}  {d['name']:<40} {'/'.join(kinds)}")
