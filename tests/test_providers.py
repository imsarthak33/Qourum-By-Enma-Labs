"""Provider adapter (mocked HTTP) + registry fallback + breaker — 02_TRD §11."""

from __future__ import annotations

import json

import httpx
import pytest

from quorum.errors import CouncilError
from quorum.providers import (
    ChatRequest,
    CircuitBreaker,
    Message,
    OpenAICompatProvider,
    ProviderRegistry,
)


def _ok_transport(text: str = "hello") -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"]
        assert request.headers["Authorization"].startswith("Bearer ")
        return httpx.Response(200, json={
            "choices": [{"message": {"content": text}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        })
    return httpx.MockTransport(handler)


def _failing_transport(status: int = 500) -> httpx.MockTransport:
    return httpx.MockTransport(lambda _: httpx.Response(status, json={"error": "boom"}))


def _req() -> ChatRequest:
    return ChatRequest(model="m1", messages=[Message("user", "hi")])


class TestOpenAICompatProvider:
    async def test_complete_parses_response(self):
        p = OpenAICompatProvider("test", "https://api.test/v1", "key",
                                 transport=_ok_transport("narration text"))
        resp = await p.complete(_req())
        assert resp.text == "narration text"
        assert resp.tokens_in == 10 and resp.tokens_out == 5
        assert resp.provider == "test"

    async def test_http_error_raises(self):
        p = OpenAICompatProvider("test", "https://api.test/v1", "key",
                                 transport=_failing_transport())
        with pytest.raises(httpx.HTTPStatusError):
            await p.complete(_req())

    async def test_json_mode_sets_response_format(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content))
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "{}"}}], "usage": {},
            })

        p = OpenAICompatProvider("test", "https://api.test/v1", "key",
                                 transport=httpx.MockTransport(handler))
        req = _req()
        req.response_format = "json"
        await p.complete(req)
        assert captured["response_format"] == {"type": "json_object"}


def _registry(providers, profiles=None) -> ProviderRegistry:
    profiles = profiles or {
        "free": {
            "technician": {
                "primary": {"p": "a", "m": "model-a"},
                "fallbacks": [{"p": "b", "m": "model-b"}],
            }
        }
    }
    return ProviderRegistry(providers=providers, profiles=profiles)


class TestRegistryFallback:
    async def test_primary_success_no_fallback(self):
        reg = _registry({
            "a": OpenAICompatProvider("a", "https://a/v1", "k", transport=_ok_transport("from-a")),
            "b": OpenAICompatProvider("b", "https://b/v1", "k", transport=_ok_transport("from-b")),
        })
        resp, fell_back = await reg.call_agent("free", "technician", _req())
        assert resp.text == "from-a" and not fell_back

    async def test_cross_vendor_fallback(self):
        reg = _registry({
            "a": OpenAICompatProvider("a", "https://a/v1", "k", transport=_failing_transport()),
            "b": OpenAICompatProvider("b", "https://b/v1", "k", transport=_ok_transport("from-b")),
        })
        resp, fell_back = await reg.call_agent("free", "technician", _req())
        assert resp.text == "from-b" and fell_back

    async def test_whole_chain_down_raises_council_error(self):
        reg = _registry({
            "a": OpenAICompatProvider("a", "https://a/v1", "k", transport=_failing_transport()),
            "b": OpenAICompatProvider("b", "https://b/v1", "k", transport=_failing_transport()),
        })
        with pytest.raises(CouncilError):
            await reg.call_agent("free", "technician", _req())

    async def test_unconfigured_provider_skipped(self):
        # only "b" has a key/instance; primary "a" silently drops from the chain
        reg = _registry({
            "b": OpenAICompatProvider("b", "https://b/v1", "k", transport=_ok_transport("from-b")),
        })
        resp, _ = await reg.call_agent("free", "technician", _req())
        assert resp.text == "from-b"

    async def test_no_providers_at_all(self):
        reg = _registry({})
        with pytest.raises(CouncilError):
            await reg.call_agent("free", "technician", _req())


class TestCircuitBreaker:
    def test_opens_after_threshold(self):
        b = CircuitBreaker(failure_threshold=3, cooldown_s=60)
        for _ in range(3):
            b.record_failure("x")
        assert b.is_open("x")

    def test_success_resets(self):
        b = CircuitBreaker(failure_threshold=3)
        b.record_failure("x")
        b.record_failure("x")
        b.record_success("x")
        b.record_failure("x")
        assert not b.is_open("x")

    def test_half_open_after_cooldown(self):
        b = CircuitBreaker(failure_threshold=1, cooldown_s=0.0)
        b.record_failure("x")
        assert not b.is_open("x")  # cooldown elapsed instantly -> probe allowed
