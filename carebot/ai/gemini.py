"""Gemini, used in the middle: phrasing lines and understanding speech. Text only, never images.

Every call has a hard timeout and returns None on any problem (no key, offline, slow, bad JSON),
so callers always fall back to CareBot's templates and keyword rules. Gemini never decides what
CareBot does; the state machine does (see carebot/core/state.py).
"""

from __future__ import annotations

import json
import os
import statistics
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from carebot.config import load_env

# Tried in order if the configured model isn't available to this key.
FALLBACK_MODELS = ("gemini-flash-lite-latest", "gemini-flash-latest", "gemini-2.5-flash-lite", "gemini-2.5-flash")


class NoAI:
    """Gemini switched off (no key, or ai.enabled: false). Everything falls back to templates."""

    name = "off (templates + keywords)"
    enabled = False

    def generate(self, system: str, prompt: str, schema: dict | None = None, **kw):
        return None

    def typical_ms(self) -> float | None:
        return None


class Gemini:
    enabled = True

    def __init__(self, cfg: dict, api_key: str) -> None:
        from google import genai  # lazy: only when AI is on

        self.client = genai.Client(api_key=api_key)
        self.model = cfg.get("model", FALLBACK_MODELS[0])
        self.timeout = float(cfg.get("timeout_s", 1.8))
        self.thinking = cfg.get("thinking")  # e.g. {"thinking_budget": 0}; None = model default
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="gemini")
        self.latencies: deque[float] = deque(maxlen=50)
        self.failures = 0

    @property
    def name(self) -> str:
        return f"Gemini {self.model}"

    def _call(self, system: str, prompt: str, schema: dict | None, temperature: float, max_tokens: int) -> str:
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            max_output_tokens=max_tokens,
            response_mime_type="application/json" if schema else None,
            response_json_schema=schema,
            thinking_config=types.ThinkingConfig(**self.thinking) if self.thinking else None,
        )
        r = self.client.models.generate_content(model=self.model, contents=prompt, config=config)
        return r.text or ""

    def generate(self, system: str, prompt: str, schema: dict | None = None, temperature: float = 0.4,
                 max_tokens: int = 300, timeout: float | None = None):
        """Text (or a dict when `schema` is given), or None on any problem within the timeout."""
        start = time.monotonic()
        future = self.pool.submit(self._call, system, prompt, schema, temperature, max_tokens)
        try:
            text = future.result(timeout=timeout or self.timeout)
        except FutureTimeout:
            self.failures += 1
            return None
        except Exception:  # noqa: BLE001 - offline, quota, bad model...: fall back
            self.failures += 1
            return None
        self.latencies.append((time.monotonic() - start) * 1000)
        if not schema:
            return text.strip() or None
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            self.failures += 1
            return None
        return data if isinstance(data, dict) else None

    def typical_ms(self) -> float | None:
        return statistics.median(self.latencies) if self.latencies else None

    def probe(self) -> str | None:
        """One tiny request with a generous timeout. Tries fallback models if the configured one
        fails. Returns an error message, or None if Gemini works."""
        tried = []
        for model in (self.model, *[m for m in FALLBACK_MODELS if m != self.model]):
            self.model = model
            if self.generate("Reply with the single word: ready", "Are you there?", max_tokens=10, timeout=8) is not None:
                return None
            tried.append(model)
            if self.thinking:  # some models reject thinking settings: retry once without them
                self.thinking = None
                if self.generate("Reply with the single word: ready", "Are you there?", max_tokens=10, timeout=8):
                    return None
        return f"no model answered (tried {', '.join(tried)})"


def safe_generate(ai, *args, **kwargs):
    """ai.generate(...), but any exception means None (fall back)."""
    try:
        return ai.generate(*args, **kwargs)
    except Exception:  # noqa: BLE001
        return None


def make_ai(cfg: dict):
    """Gemini if enabled and a key is set (GEMINI_API_KEY in .env), otherwise NoAI."""
    ai_cfg = cfg.get("ai", {})
    if not ai_cfg.get("enabled", False):
        return NoAI()
    load_env()
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        print("AI: no GEMINI_API_KEY in .env, so CareBot uses its templates and keyword rules.", flush=True)
        return NoAI()
    try:
        ai = Gemini(ai_cfg, key)
    except Exception as e:  # noqa: BLE001 - SDK missing etc.
        print(f"AI: Gemini unavailable ({e}); using templates.", flush=True)
        return NoAI()
    problem = ai.probe()
    if problem:
        print(f"AI: Gemini not reachable ({problem}); using templates.", flush=True)
        return NoAI()
    ms = ai.typical_ms()
    print(f"AI: {ai.name} ready (first reply {ms:.0f} ms)", flush=True)
    return ai
