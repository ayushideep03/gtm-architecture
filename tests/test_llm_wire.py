"""
LLM adapters exercised through the real vendor SDKs (openai, anthropic) with a fake HTTP
transport: proves the request each adapter builds is accepted by the installed SDK, has the
expected wire shape, and that responses/refusals/truncation are parsed. No network, no key.

This is NOT a live test: it cannot show that the configured model names exist or that the
vendor accepts the schema server-side. It does check every schema we send against OpenAI's
documented strict-mode rules, which the server would otherwise reject.
"""

import json

import pytest

from app.llm.base import LLMError, LLMRefusal

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"],
          "additionalProperties": False}
CALL = dict(model="m", system="sys", user="usr", schema=SCHEMA, schema_name="x", max_output_tokens=200,
            timeout_s=5, temperature=None, effort=None, refusal_fallbacks=None, max_retries=0)


def _anthropic_message(text=None, stop="end_turn"):
    content = [{"type": "text", "text": text}] if text is not None else []
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": content,
            "stop_reason": stop, "stop_sequence": None, "usage": {"input_tokens": 5, "output_tokens": 3}}


def _openai_completion(content=None, finish="stop", refusal=None):
    return {"id": "c1", "object": "chat.completion", "created": 1, "model": "gpt-4.1",
            "choices": [{"index": 0, "finish_reason": finish,
                         "message": {"role": "assistant", "content": content, "refusal": refusal}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}}


@pytest.fixture
def anthropic_wire(monkeypatch):
    anthropic = pytest.importorskip("anthropic")
    try:
        import httpx2 as hx           # the installed anthropic SDK ships its own httpx fork
    except ImportError:
        import httpx as hx
    import app.llm.anthropic_client as mod
    state = {"requests": [], "reply": _anthropic_message(json.dumps({"ok": True}))}

    def handler(request):
        state["requests"].append((request.url.path, json.loads(request.content), request.headers.get("anthropic-beta")))
        return hx.Response(200, json=state["reply"])

    original = anthropic.AsyncAnthropic
    monkeypatch.setattr(mod, "AsyncAnthropic", lambda **kw: original(
        **kw, http_client=hx.AsyncClient(transport=hx.MockTransport(handler))))
    return state


@pytest.fixture
def openai_wire(monkeypatch):
    openai = pytest.importorskip("openai")
    import httpx
    import app.llm.openai_client as mod
    state = {"requests": [], "reply": _openai_completion(json.dumps({"ok": True}))}

    def handler(request):
        state["requests"].append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json=state["reply"])

    original = openai.AsyncOpenAI
    monkeypatch.setattr(mod, "AsyncOpenAI", lambda **kw: original(
        **kw, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))))
    return state


class TestAnthropicAdapterWire:
    @pytest.mark.anyio
    async def test_structured_output_request_and_parse(self, anthropic_wire):
        from app.llm.anthropic_client import AnthropicJSONClient
        data, usage, model = await AnthropicJSONClient("sk-test").complete_json(**{**CALL, "effort": "medium"})
        assert data == {"ok": True} and model == "claude-opus-5-5" and usage["input_tokens"] == 5
        path, body, beta = anthropic_wire["requests"][0]
        assert path == "/v1/messages" and beta is None
        assert body["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA}, "effort": "medium"}
        assert body["system"] == "sys" and body["messages"] == [{"role": "user", "content": "usr"}]

    @pytest.mark.anyio
    async def test_refusal_fallbacks_use_beta_header(self, anthropic_wire):
        from app.llm.anthropic_client import FALLBACK_BETA, AnthropicJSONClient
        await AnthropicJSONClient("sk-test").complete_json(**{**CALL, "refusal_fallbacks": [{"model": "claude-sonnet-5-5"}]})
        _, body, beta = anthropic_wire["requests"][0]
        assert beta == FALLBACK_BETA and body["fallbacks"] == [{"model": "claude-sonnet-5-5"}]

    @pytest.mark.anyio
    async def test_refusal_and_truncation(self, anthropic_wire):
        from app.llm.anthropic_client import AnthropicJSONClient
        anthropic_wire["reply"] = _anthropic_message(None, stop="refusal")
        with pytest.raises(LLMRefusal):
            await AnthropicJSONClient("sk-test").complete_json(**CALL)
        anthropic_wire["reply"] = _anthropic_message('{"ok": tr', stop="max_tokens")
        with pytest.raises(LLMError):
            await AnthropicJSONClient("sk-test").complete_json(**CALL)


class TestOpenAIAdapterWire:
    @pytest.mark.anyio
    async def test_strict_json_schema_request_and_parse(self, openai_wire):
        from app.llm.openai_client import OpenAIJSONClient
        data, _, model = await OpenAIJSONClient("sk-test").complete_json(**CALL)
        assert data == {"ok": True} and model == "gpt-4.1"
        path, body = openai_wire["requests"][0]
        assert path == "/v1/chat/completions"
        assert body["response_format"] == {"type": "json_schema",
                                           "json_schema": {"name": "x", "schema": SCHEMA, "strict": True}}
        assert body["messages"][0] == {"role": "system", "content": "sys"} and body["max_completion_tokens"] == 200

    @pytest.mark.anyio
    async def test_refusal_and_truncation(self, openai_wire):
        from app.llm.openai_client import OpenAIJSONClient
        openai_wire["reply"] = _openai_completion(None, refusal="I can't help with that")
        with pytest.raises(LLMRefusal):
            await OpenAIJSONClient("sk-test").complete_json(**CALL)
        openai_wire["reply"] = _openai_completion('{"ok"', finish="length")
        with pytest.raises(LLMError):
            await OpenAIJSONClient("sk-test").complete_json(**CALL)


def _strict_violations(schema, path="$"):
    """OpenAI strict mode: every object lists all properties as required and forbids extras."""
    out = []
    if isinstance(schema, dict):
        types = schema.get("type")
        if types == "object" or (isinstance(types, list) and "object" in types):
            props = schema.get("properties", {})
            if schema.get("additionalProperties") is not False:
                out.append(f"{path}: additionalProperties must be false")
            if set(schema.get("required", [])) != set(props):
                out.append(f"{path}: required must list every property")
        for key, value in schema.items():
            if key in ("properties", "$defs", "definitions"):
                for name, sub in value.items():
                    out += _strict_violations(sub, f"{path}.{name}")
            elif key in ("items", "anyOf", "oneOf", "allOf"):
                for i, sub in enumerate(value if isinstance(value, list) else [value]):
                    out += _strict_violations(sub, f"{path}.{key}[{i}]")
    return out


def test_every_schema_we_send_is_strict_mode_compatible():
    from app.capabilities.llm_providers import (LLMDraftingProvider, LLMIntentProvider, LLMQualificationProvider,
                                                LLMResearchProvider)
    from app.events.inbound import _reply_schema
    from app.oxygen.planner import proposal_schema
    schemas = {
        "prospect_research": LLMResearchProvider.schema(), "intent_assessment": LLMIntentProvider.schema(),
        "qualification": LLMQualificationProvider.schema(), "outreach_draft": LLMDraftingProvider.schema(),
        "reply_classification": _reply_schema(),
        "action_proposal": proposal_schema(["wait", "stop", "escalate", "send_email", "qualify_lead"]),
    }
    problems = {name: _strict_violations(s) for name, s in schemas.items()}
    assert not any(problems.values()), problems
