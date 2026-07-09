"""Universal adapter for OpenAI-chat-completions-compatible vendors.

Covers OpenRouter, NVIDIA NIM, Groq, Together, Fireworks, self-hosted NIM,
and Google AI Studio's OpenAI-compat endpoint — parameterised only by
base_url + api_key (05_AI_ARCHITECTURE §3.2).
"""

from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from .base import ChatChunk, ChatRequest, ChatResponse, ProviderHealth


class OpenAICompatProvider:
    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str,
        default_headers: dict | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.name = name
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._headers = default_headers or {}
        self._client = httpx.AsyncClient(timeout=30.0, transport=transport)

    def _body(self, req: ChatRequest, stream: bool = False) -> dict:
        body: dict = {
            "model": req.model,
            "messages": [{"role": m.role, "content": m.content} for m in req.messages],
            "temperature": req.temperature,
            "max_tokens": req.max_tokens,
        }
        if req.response_format == "json":
            body["response_format"] = {"type": "json_object"}
        if stream:
            body["stream"] = True
        body.update(req.extra)
        return body

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self._key}", **self._headers}

    async def complete(self, req: ChatRequest) -> ChatResponse:
        r = await self._client.post(
            f"{self._base}/chat/completions",
            headers=self._auth(),
            json=self._body(req),
            timeout=req.timeout_s,
        )
        r.raise_for_status()
        data = r.json()
        usage = data.get("usage") or {}
        return ChatResponse(
            text=data["choices"][0]["message"]["content"],
            tokens_in=usage.get("prompt_tokens", 0),
            tokens_out=usage.get("completion_tokens", 0),
            model=req.model,
            provider=self.name,
        )

    async def stream(self, req: ChatRequest) -> AsyncIterator[ChatChunk]:
        async with self._client.stream(
            "POST",
            f"{self._base}/chat/completions",
            headers=self._auth(),
            json=self._body(req, stream=True),
            timeout=req.timeout_s,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload.strip() == "[DONE]":
                    yield ChatChunk(delta="", done=True)
                    return
                choice = json.loads(payload)["choices"][0]
                delta = (choice.get("delta") or {}).get("content", "")
                if delta:
                    yield ChatChunk(delta=delta)
        yield ChatChunk(delta="", done=True)

    async def health(self) -> ProviderHealth:
        try:
            r = await self._client.get(f"{self._base}/models", headers=self._auth(), timeout=5.0)
            return ProviderHealth(healthy=r.status_code < 500)
        except httpx.HTTPError:
            return ProviderHealth(healthy=False)

    async def aclose(self) -> None:
        await self._client.aclose()
