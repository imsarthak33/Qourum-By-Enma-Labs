"""Provider contract (05_AI_ARCHITECTURE §3.1, 02_TRD §3).

Every model call goes through CouncilProvider. Providers are narration-only:
no probability, weight, or decision ever originates from a provider call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Literal, Protocol, runtime_checkable


@dataclass
class Message:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass
class ChatRequest:
    model: str
    messages: list[Message]
    temperature: float = 0.4
    max_tokens: int = 900
    response_format: Literal["text", "json"] = "text"
    timeout_s: float = 20.0
    extra: dict = field(default_factory=dict)  # provider-specific passthrough


@dataclass
class ChatChunk:
    delta: str
    done: bool = False


@dataclass
class ChatResponse:
    text: str
    tokens_in: int
    tokens_out: int
    model: str
    provider: str


@dataclass
class ProviderHealth:
    healthy: bool
    p95_latency_ms: int | None = None
    error_rate: float | None = None


@runtime_checkable
class CouncilProvider(Protocol):
    name: str

    async def complete(self, req: ChatRequest) -> ChatResponse: ...

    async def stream(self, req: ChatRequest) -> AsyncIterator[ChatChunk]: ...

    async def health(self) -> ProviderHealth: ...
