"""Speech out. Every engine's `say()` blocks until the line has been spoken.

    say    macOS built-in voice (laptop)
    espeak espeak-ng (Raspberry Pi; Piper can replace it later)
    print  no sound, just prints (tests, headless)
"""

from __future__ import annotations

import shutil
import subprocess


class PrintTTS:
    def say(self, text: str, loud: bool = False) -> None:
        print(f"    [speaker{' LOUD' if loud else ''}] {text}", flush=True)

    def cue(self) -> None:
        """A short sound meaning "I'm listening now"."""


class SayTTS:
    """macOS `say`. Loud asks are spoken a little slower and with emphasis."""

    def __init__(self, cfg: dict) -> None:
        if not shutil.which("say"):
            raise RuntimeError("`say` not found (macOS only). Set voice.tts to espeak or print.")
        self.rate = str(cfg.get("rate_wpm", 175))
        self.voice = cfg.get("say_voice")
        self.device = cfg.get("output_device")  # name prefix, e.g. "MacBook Pro Speakers"; None = system default

    def say(self, text: str, loud: bool = False) -> None:
        cmd = ["say", "-r", str(int(int(self.rate) * (0.85 if loud else 1.0)))]
        if self.voice:
            cmd += ["-v", self.voice]
        if self.device:
            cmd += ["-a", self.device]
        subprocess.run(cmd + [text], check=False)

    def cue(self) -> None:
        """A soft tick: "I'm listening now, go ahead"."""
        sound = "/System/Library/Sounds/Tink.aiff"
        if self.device is None and shutil.which("afplay"):
            subprocess.run(["afplay", "-v", "0.6", sound], check=False)
        else:
            subprocess.run(["say", *(["-a", self.device] if self.device else []), "[[volm 0.4]] mm"], check=False)


class EspeakTTS:
    def __init__(self, cfg: dict) -> None:
        self.bin = shutil.which("espeak-ng") or shutil.which("espeak")
        if not self.bin:
            raise RuntimeError("espeak-ng not found: sudo apt install espeak-ng")
        self.rate = str(cfg.get("rate_wpm", 160))

    def say(self, text: str, loud: bool = False) -> None:
        amp = "200" if loud else "150"
        subprocess.run([self.bin, "-s", self.rate, "-a", amp, text], check=False)

    def cue(self) -> None:
        subprocess.run([self.bin, "-a", "60", "mm"], check=False)


def make_tts(cfg: dict):
    engine = cfg.get("tts", "print")
    try:
        return {"say": SayTTS, "espeak": EspeakTTS}[engine](cfg) if engine != "print" else PrintTTS()
    except (KeyError, RuntimeError) as e:
        print(f"TTS {engine!r} unavailable ({e}); printing speech instead.", flush=True)
        return PrintTTS()
