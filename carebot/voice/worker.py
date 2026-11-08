"""Runs Say/Ask actions one at a time on its own thread and posts the answers back as events.

A newer Ask supersedes an older one: if the button is pressed mid-question, CareBot stops waiting
for the old answer and asks the new question. Answers carry the ask_id they belong to, and the state
machine ignores stale ones anyway.

With Claude on (carebot/ai):
- each line is polished before it's spoken, and only if the rewrite passes the guardrail;
- answers are heard as whole sentences (Vosk transcript) and understood by Claude, with the keyword
  rules as the safety net.
Without it, lines are spoken word for word and answers are matched by keywords.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Callable

from carebot.core import state as st
from carebot.voice.keywords import check_in_intent, report_status_by_keywords

DIM, RESET = "\033[2m", "\033[0m"


class VoiceWorker(threading.Thread):
    def __init__(self, tts, listener, post: Callable[[object], None], listen_s: float = 15,
                 classify_report: Callable[[str], str] = report_status_by_keywords,
                 clock: Callable[[], float] = time.time, phraser=None, understander=None,
                 comfort_listen_s: float = 10, verbose: bool = False) -> None:
        super().__init__(daemon=True, name="voice")
        self.tts, self.listener, self.post = tts, listener, post
        self.listen_s = listen_s
        self.comfort_listen_s = comfort_listen_s
        self.classify_report = classify_report
        self.clock = clock
        self.phraser = phraser
        self.understander = understander
        self.verbose = verbose
        self.q: queue.Queue = queue.Queue()
        self.latest_ask = 0
        self.busy = threading.Event()

    def submit(self, action) -> None:
        if isinstance(action, st.Ask):
            self.latest_ask = action.ask_id
        self.q.put(action)

    def run(self) -> None:
        while True:
            a = self.q.get()
            if a is None:
                return
            self.busy.set()
            try:
                if isinstance(a, st.Say):
                    self._speak(a.text, heard=getattr(a, "heard", ""))
                elif isinstance(a, st.Ask):
                    self._ask(a)
            except Exception as e:  # noqa: BLE001 - never kill the voice thread mid-demo
                print(f"voice error: {e}", flush=True)
            finally:
                self.busy.clear()

    def _speak(self, text: str, loud: bool = False, heard: str = "") -> None:
        line, polished = (self.phraser.polish(text, heard) if self.phraser else (text, False))
        if self.verbose or polished:
            print(f"{DIM}    speaking ({'Claude' if polished else 'template'}): {line}{RESET}", flush=True)
        self.tts.say(line, loud=loud)

    def _ask(self, a: st.Ask) -> None:
        stale = lambda: a.ask_id != self.latest_ask  # noqa: E731
        if stale():
            return
        self._speak(a.text, loud=a.loud)
        if stale():
            return
        if hasattr(self.tts, "cue"):
            self.tts.cue()  # "go ahead, I'm listening"
        understanding = self.understander is not None and self.understander.enabled
        mode = "transcript" if understanding else a.listen
        timeout = self.comfort_listen_s if getattr(a, "kind", "") == "comfort" else self.listen_s
        text = self.listener.listen(mode, timeout, cancelled=stale)
        if stale():
            return
        now = self.clock()
        answer = getattr(a, "answer", "") or ("report" if a.listen == "transcript" else "intent")
        if not text:
            print(f"{DIM}    heard: nothing within {timeout:g} s{RESET}", flush=True)
            self.post(st.Silence(a.ask_id, now))
        elif answer == "report":
            status = self.understander.report(text) if understanding else self.classify_report(text)
            self._note(text, status)
            self.post(st.Reported(status, text, "in_room", now, a.ask_id))
        else:
            intent = self.understander.check_in(text) if understanding else check_in_intent(text)
            self._note(text, intent)
            self.post(st.Heard(a.ask_id, intent, text, now))

    def _note(self, text: str, label: str) -> None:
        """Always show what was heard and how it was understood (so a mis-hearing is obvious)."""
        how = self.understander.last_source if self.understander is not None and self.understander.enabled else "keywords"
        print(f"    heard: \"{text}\" -> {label} ({how})", flush=True)

    def stop(self) -> None:
        self.q.put(None)
