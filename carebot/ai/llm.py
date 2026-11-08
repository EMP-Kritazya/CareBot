"""Claude, used in the middle: phrasing lines, understanding speech, answering the 911 dispatcher.

Claude never decides what CareBot does; the state machine does (carebot/core/state.py). Claude only
makes lines sound human and understands what people say, and every output passes a code check
(`check_line`, `check_answer`) or the template / keyword fallback is used. Text only: no camera
frames or face data are ever sent.

Every call has a hard wall-clock timeout and returns None on any problem (no key, offline, slow,
rate limited, refused, truncated, bad JSON), so callers always fall back. Nothing retries inside a
call: on a phone line, a fast fallback beats a slow retry.

The key lives in .env as ANTHROPIC_API_KEY (git-ignored).
"""

from __future__ import annotations

import copy
import json
import os
import statistics
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from carebot.config import load_env

DEFAULT_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"  # pairs with fallbacks="default" (scalar form)


class NoAI:
    """Claude switched off (no key, or ai.enabled: false). Everything falls back to templates."""

    name = "off (templates + keywords)"
    enabled = False

    def generate(self, system: str, prompt: str, schema: dict | None = None, **kw):
        return None

    def typical_ms(self) -> float | None:
        return None


def strict_schema(schema: dict) -> dict:
    """Structured outputs need every object to forbid extra keys and list its required keys."""
    s = copy.deepcopy(schema)

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                node.setdefault("additionalProperties", False)
                node.setdefault("required", list(node.get("properties", {})))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(s)
    return s


class ClaudeAI:
    enabled = True

    def __init__(self, cfg: dict, api_key: str) -> None:
        import anthropic  # lazy: only when AI is on

        self._anthropic = anthropic
        self.model = cfg.get("model", DEFAULT_MODEL)
        self.effort = cfg.get("effort", "low")          # low = fastest; Opus 5.5's default would be medium
        self.timeout = float(cfg.get("timeout_s", 4))                        # phrasing + understanding
        self.dispatcher_timeout = float(cfg.get("dispatcher_timeout_s", 6))  # 911 answers (a pause is OK)
        self.refusal_fallbacks = bool(cfg.get("refusal_fallbacks", True))
        # max_retries=0: the SDK would otherwise retry 429/5xx with backoff; we fall back instead.
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=0, timeout=self.timeout + 1)
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="claude")
        self.latencies: deque[float] = deque(maxlen=50)
        self.failures = 0
        self.last_error: str | None = None

    @property
    def name(self) -> str:
        return f"Claude {self.model}"

    def _call(self, system: str, prompt: str, schema: dict | None, max_tokens: int, context: str | None,
              timeout: float) -> str | None:
        # Stable text first, so repeated requests can reuse the prompt cache: the system prompt, then
        # (for the 911 agent) the incident's fact sheet; the changing question goes last.
        blocks = [{"type": "text", "text": system}]
        if context:
            blocks.append({"type": "text", "text": context})
        blocks[-1]["cache_control"] = {"type": "ephemeral"}
        output_config: dict = {"effort": self.effort}
        if schema:
            output_config["format"] = {"type": "json_schema", "schema": strict_schema(schema)}
        kwargs = dict(model=self.model, max_tokens=max_tokens, system=blocks,
                      messages=[{"role": "user", "content": prompt}], output_config=output_config)
        client = self.client.with_options(timeout=timeout)
        if self.refusal_fallbacks:
            r = client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
        else:
            r = client.messages.create(**kwargs)
        if r.stop_reason in ("refusal", "max_tokens"):  # declined, or cut off before valid JSON
            self.last_error = f"stop_reason={r.stop_reason}"
            return None
        return next((b.text for b in r.content if b.type == "text"), None)

    def generate(self, system: str, prompt: str, schema: dict | None = None, max_tokens: int = 2000,
                 timeout: float | None = None, context: str | None = None, **_ignored):
        """Text (or a dict when `schema` is given), or None on any problem within the timeout.
        `context`: stable text placed after the system prompt (cached), e.g. a fact sheet."""
        wait = timeout or self.timeout
        start = time.monotonic()
        future = self.pool.submit(self._call, system, prompt, schema, max_tokens, context, wait)
        try:
            text = future.result(timeout=wait)
        except FutureTimeout:
            self.failures += 1
            self.last_error = f"slower than {wait:g} s"
            return None
        except self._anthropic.APIError as e:  # rate limit, auth, overload, network: fall back
            self.failures += 1
            self.last_error = f"{type(e).__name__}: {str(e)[:120]}"
            return None
        except Exception as e:  # noqa: BLE001 - never let the AI layer break CareBot
            self.failures += 1
            self.last_error = f"{type(e).__name__}: {str(e)[:120]}"
            return None
        if text is None:
            self.failures += 1
            return None
        self.latencies.append((time.monotonic() - start) * 1000)
        if not schema:
            return text.strip() or None
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            self.failures += 1
            self.last_error = "reply wasn't valid JSON"
            return None
        return data if isinstance(data, dict) else None

    def typical_ms(self) -> float | None:
        return statistics.median(self.latencies) if self.latencies else None

    def probe(self) -> str | None:
        """One tiny request with a generous timeout. Returns an error message, or None if Claude works."""
        out = self.generate("You check that a connection works.", 'Reply with JSON {"ok": true}.',
                            {"type": "object", "properties": {"ok": {"type": "boolean"}}}, timeout=20)
        return None if isinstance(out, dict) and out.get("ok") else (self.last_error or "no valid reply")


def safe_generate(ai, *args, **kwargs):
    """ai.generate(...), but any exception means None (fall back)."""
    try:
        return ai.generate(*args, **kwargs)
    except Exception:  # noqa: BLE001
        return None


def make_ai(cfg: dict):
    """Claude if enabled and a key is set (ANTHROPIC_API_KEY in .env), otherwise NoAI."""
    ai_cfg = cfg.get("ai", {})
    if not ai_cfg.get("enabled", False):
        return NoAI()
    load_env()
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        print("AI: no ANTHROPIC_API_KEY in .env, so CareBot uses its templates and keyword rules.", flush=True)
        return NoAI()
    try:
        ai = ClaudeAI(ai_cfg, key)
    except Exception as e:  # noqa: BLE001 - SDK missing etc.
        print(f"AI: Claude unavailable ({e}); using templates.", flush=True)
        return NoAI()
    problem = ai.probe()
    if problem:
        print(f"AI: Claude not reachable ({problem}); using templates.", flush=True)
        return NoAI()
    print(f"AI: {ai.name} ready (first reply {ai.typical_ms():.0f} ms)", flush=True)
    return ai
