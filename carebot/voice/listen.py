"""Speech in. `Listener.listen()` waits for one answer: from the microphone (Vosk) or typed.

Two modes:
    keywords    check-ins: Vosk is limited to a few phrases ("I'm okay", "help", ...), which is far
                more accurate than open dictation in a quiet room
    transcript  "Who are you, and what's happening?": open dictation, the whole sentence

Typed answers always work (laptop testing, or a mic failure during the demo).

The microphone stream opens once and stays open. Opening a Bluetooth mic (AirPods) switches it into
call mode, which takes a second or two, so opening it per question used to lose the start of every
answer. Audio is only *used* after CareBot has finished speaking, and anything older is thrown away,
so CareBot never hears its own "Say 'I'm OK' or 'help'" as an answer.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from typing import Callable

from carebot.config import ROOT

KEYWORD_GRAMMAR = ["i'm okay", "i am okay", "okay", "i'm fine", "i am fine", "fine", "i'm alright",
                   "yes", "yeah", "help", "help me", "i need help", "no", "i can't get up", "[unk]"]


class VoskMic:
    def __init__(self, cfg: dict) -> None:
        import sounddevice  # noqa: F401  (fail early if missing)
        from vosk import Model, SetLogLevel

        SetLogLevel(-1)
        path = ROOT / cfg.get("vosk_model", "models/vosk-model-small-en-us-0.15")
        if not path.exists():
            raise RuntimeError(f"Vosk model not found at {path}. Download vosk-model-small-en-us-0.15 into models/.")
        self.model = Model(str(path))
        self.device = cfg.get("input_device")
        self.rate = int(cfg.get("input_rate", 16000))
        self.grammar = bool(cfg.get("grammar", False))
        self.audio: queue.Queue[bytes] = queue.Queue(maxsize=200)
        self.capturing = threading.Event()
        self._stream = None

    def recognizer(self, mode: str):
        """Open dictation in both modes. A phrase-list grammar forces every sound (a cough, a half
        word) into one of the phrases, which turned noise into "okay"; the keyword rules applied to
        the real transcript are safer. Set voice.grammar: true to bring the phrase list back."""
        from vosk import KaldiRecognizer

        if mode == "keywords" and self.grammar:
            return KaldiRecognizer(self.model, self.rate, json.dumps(KEYWORD_GRAMMAR))
        return KaldiRecognizer(self.model, self.rate)

    def open(self) -> None:
        """Start the always-on stream (once). Audio is queued only while `capturing` is set."""
        if self._stream is not None:
            return
        import sounddevice as sd

        def callback(indata, frames, t, status):  # runs on the audio thread: just hand off bytes
            if self.capturing.is_set():
                try:
                    self.audio.put_nowait(bytes(indata))
                except queue.Full:
                    pass

        self._stream = sd.RawInputStream(samplerate=self.rate, blocksize=4000, dtype="int16", channels=1,
                                         callback=callback, device=self.device)
        self._stream.start()

    def drain(self) -> None:
        while not self.audio.empty():
            try:
                self.audio.get_nowait()
            except queue.Empty:
                return

    def close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None


class Listener:
    def __init__(self, mic: VoskMic | None = None, settle_s: float = 0.3) -> None:
        self.mic = mic
        self.settle_s = settle_s
        self.typed: queue.Queue[str] = queue.Queue()
        self.listening = threading.Event()
        self.mode: str | None = None

    def type(self, text: str) -> bool:
        """A typed answer (from the console). Returns False if nobody is listening right now."""
        if not self.listening.is_set():
            return False
        self.typed.put(text)
        return True

    def listen(self, mode: str, timeout: float, cancelled: Callable[[], bool] = lambda: False) -> str | None:
        """Wait up to `timeout` seconds for an answer. None = silence (or cancelled)."""
        while not self.typed.empty():
            self.typed.get_nowait()
        time.sleep(self.settle_s)  # let the speaker's last syllable fade before using the mic
        self.mode = mode
        self.listening.set()
        rec = None
        audio = self.mic.audio if self.mic is not None else queue.Queue()
        try:
            if self.mic is not None:
                self.mic.open()
                self.mic.drain()          # throw away anything from before (CareBot's own voice)
                self.mic.capturing.set()
                rec = self.mic.recognizer(mode)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and not cancelled():
                try:
                    return self.typed.get_nowait()
                except queue.Empty:
                    pass
                try:
                    chunk = audio.get(timeout=0.1)
                except queue.Empty:
                    continue
                if rec is not None and rec.AcceptWaveform(chunk):
                    text = _clean(json.loads(rec.Result()).get("text", ""))
                    if text:
                        return text
            if rec is not None and not cancelled():
                return _clean(json.loads(rec.FinalResult()).get("text", "")) or None
            return None
        finally:
            self.listening.clear()
            self.mode = None
            if self.mic is not None:
                self.mic.capturing.clear()


def _clean(text: str) -> str:
    return " ".join(w for w in text.split() if w != "[unk]").strip()


def make_listener(cfg: dict) -> Listener:
    mic = None
    if cfg.get("mic", True):
        try:
            mic = VoskMic(cfg)
            mic.open()  # open now, so a Bluetooth mic is already in call mode before the first question
        except Exception as e:  # noqa: BLE001 - typed answers still work
            print(f"Microphone unavailable ({e}). Type answers in the terminal instead.", flush=True)
            mic = None
    return Listener(mic)
