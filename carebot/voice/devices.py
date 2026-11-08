"""Which speaker and microphone CareBot is using, so a wrong default (e.g. AirPods) is obvious."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys

HEADPHONE_WORDS = ("airpods", "headphone", "headset", "buds", "beats")


_PROP = re.compile(r'^\s*\*?\s*([\w.]+)\s*=\s*"(.*)"\s*$')


def parse_wpctl_inspect(text: str) -> dict[str, str]:
    """Properties from `wpctl inspect` output: node.name, node.description, device.api, ..."""
    props = {}
    for line in text.splitlines():
        m = _PROP.match(line)
        if m:
            props.setdefault(m.group(1), m.group(2))
    return props


def pipewire_defaults(run=subprocess.run) -> dict[str, dict[str, str]] | None:
    """The default speaker and mic as PipeWire sees them (the robot). None without wpctl (laptops)."""
    if not sys.platform.startswith("linux") or not shutil.which("wpctl"):
        return None
    out = {}
    for kind, target in (("speaker", "@DEFAULT_AUDIO_SINK@"), ("mic", "@DEFAULT_AUDIO_SOURCE@")):
        try:
            r = run(["wpctl", "inspect", target], capture_output=True, text=True, timeout=3)
            out[kind] = parse_wpctl_inspect(r.stdout) if r.returncode == 0 else {}
        except Exception:  # noqa: BLE001 - reporting only
            out[kind] = {}
    return out


def pipewire_lines(defaults: dict[str, dict[str, str]]) -> list[str]:
    """Which speaker and mic the robot uses, and a warning when the speaker isn't the Bluetooth JBL."""
    spk, mic = defaults.get("speaker", {}), defaults.get("mic", {})
    spk_name = spk.get("node.description") or spk.get("node.name") or "none"
    mic_name = mic.get("node.description") or mic.get("node.name") or "none"
    bluetooth = spk.get("node.name", "").startswith("bluez_output") or spk.get("device.api") == "bluez5"
    lines = [f"Speaking through: {spk_name}{' (Bluetooth)' if bluetooth else ''}",
             f"Listening on:     {mic_name}"]
    if not bluetooth:
        lines.append(f"WARNING: the speaker is {spk_name}, not the Bluetooth speaker: the JBL isn't connected. "
                     "Turn it on, then `bluetoothctl connect <its address>`.")
    if mic_name == "none":
        lines.append("WARNING: no microphone. Typed answers in this terminal still work.")
    return lines


def describe_audio(output_device: str | None, input_device: str | None) -> list[str]:
    """Human-readable lines about the active audio devices, with a warning for headphones."""
    defaults = pipewire_defaults() if not (output_device or input_device) else None
    if defaults is not None:
        return pipewire_lines(defaults)
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
