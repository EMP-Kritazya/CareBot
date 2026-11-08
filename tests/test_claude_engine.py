"""The Claude engine itself (carebot/ai/llm.py), with a fake Anthropic client: request shape,
honest failure handling, timeouts. No network, no key."""

import time
import types

import anthropic
import pytest

from carebot.ai.dispatcher import answer
from carebot.ai.llm import ClaudeAI, NoAI, make_ai, strict_schema


class FakeMessages:
    def __init__(self, reply="{}", stop="end_turn", raise_exc=None, delay=0.0):
        self.reply, self.stop, self.raise_exc, self.delay, self.calls = reply, stop, raise_exc, delay, []

    def create(self, **kw):
        self.calls.append(kw)
        if self.delay:
            time.sleep(self.delay)
        if self.raise_exc:
            raise self.raise_exc
        block = types.SimpleNamespace(type="text", text=self.reply)
        return types.SimpleNamespace(stop_reason=self.stop, content=[block])


def engine(cfg=None, **fake):
    ai = ClaudeAI({"timeout_s": 2, **(cfg or {})}, api_key="test-key")
    msgs = FakeMessages(**fake)
    stub = types.SimpleNamespace(messages=msgs, beta=types.SimpleNamespace(messages=msgs),
                                 with_options=lambda **kw: stub)
    ai.client = stub
    return ai, msgs


SCHEMA = {"type": "object", "properties": {"line": {"type": "string"}}}


def test_request_uses_structured_output_low_effort_caching_and_refusal_fallback():
    ai, msgs = engine(reply='{"line": "hello"}')
    assert ai.generate("SYSTEM", "PROMPT", SCHEMA, context="FACTS") == {"line": "hello"}
    kw = msgs.calls[0]
    assert kw["model"] == "claude-opus-5-5"
    assert kw["output_config"]["effort"] == "low"
    fmt = kw["output_config"]["format"]
    assert fmt["type"] == "json_schema" and fmt["schema"]["additionalProperties"] is False
    assert [b["text"] for b in kw["system"]] == ["SYSTEM", "FACTS"]
    assert kw["system"][-1]["cache_control"] == {"type": "ephemeral"}  # stable prefix cached
    assert kw["messages"] == [{"role": "user", "content": "PROMPT"}]
    assert kw["betas"] == ["server-side-fallback-2026-07-01"] and kw["fallbacks"] == "default"
    assert "temperature" not in kw and "thinking" not in kw  # rejected on this model


def test_refusal_fallbacks_can_be_switched_off():
    ai, msgs = engine({"refusal_fallbacks": False}, reply='{"line": "x"}')
    ai.generate("S", "P", SCHEMA)
    assert "fallbacks" not in msgs.calls[0] and "betas" not in msgs.calls[0]


@pytest.mark.parametrize("fake", [
    {"stop": "refusal"},                                   # declined
    {"stop": "max_tokens", "reply": '{"line": "trunc'},    # cut off
    {"reply": "not json"},
    {"raise_exc": anthropic.APIConnectionError(request=None)},
    {"delay": 3.0},                                        # slower than the 2 s timeout
])
def test_every_problem_means_none_so_callers_fall_back(fake):
    ai, _ = engine(**fake)
    assert ai.generate("S", "P", SCHEMA) is None


def test_rate_limit_is_a_fallback_not_a_crash():
    import httpx2
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    err = anthropic.RateLimitError("rate limited", response=httpx2.Response(429, request=request), body=None)
    ai, _ = engine(raise_exc=err)
    assert ai.generate("S", "P", SCHEMA) is None and "RateLimitError" in ai.last_error


def test_strict_schema_closes_nested_objects():
    s = strict_schema({"type": "object", "properties": {"a": {"type": "object", "properties": {"b": {"type": "string"}}}}})
    assert s["additionalProperties"] is False and s["required"] == ["a"]
    assert s["properties"]["a"]["additionalProperties"] is False and s["properties"]["a"]["required"] == ["b"]


def test_dispatcher_sends_facts_as_the_cached_context_and_uses_its_own_timeout():
    ai, msgs = engine({"dispatcher_timeout_s": 6}, reply='{"answer": "West 115th Street.", "from_facts": true}')
    facts = {"address": "West 115th Street", "quotes": []}
    text, source = answer("Where is he?", facts, ai, history=[("What happened?", "He fell.")])
    assert source == "claude" and text == "West 115th Street."
    kw = msgs.calls[0]
    assert "West 115th Street" in kw["system"][-1]["text"] and kw["system"][-1]["cache_control"]
    assert "DISPATCHER: What happened?" in kw["messages"][0]["content"]


def test_no_key_means_templates(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setattr("carebot.ai.llm.load_env", lambda: None)
    assert isinstance(make_ai({"ai": {"enabled": True}}), NoAI)
    assert isinstance(make_ai({"ai": {"enabled": False}}), NoAI)
