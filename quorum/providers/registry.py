"""Provider registry + agent bindings + resilient call with cross-vendor
fallback (05_AI_ARCHITECTURE §3.4–3.5).

Agents are bound to (provider, model) chains via config profiles, so the whole
council can be re-pointed at different vendors without code changes.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ..errors import CouncilError
from .base import ChatRequest, ChatResponse, CouncilProvider
from .breaker import CircuitBreaker


@dataclass
class ProviderRegistry:
    providers: dict[str, CouncilProvider]
    profiles: dict[str, dict]
    breaker: CircuitBreaker

    def __init__(
        self,
        providers: dict[str, CouncilProvider],
        profiles: dict[str, dict],
        breaker: CircuitBreaker | None = None,
    ):
        self.providers = providers
        self.profiles = profiles
        self.breaker = breaker or CircuitBreaker()

    def resolve(self, profile: str, agent: str) -> list[tuple[CouncilProvider, str]]:
        """Return the [(provider, model), ...] chain for an agent, skipping
        providers the user hasn't configured a key for."""
        cfg = self.profiles.get(profile, {}).get(agent)
        if not cfg:
            return []
        chain = [cfg["primary"]] + list(cfg.get("fallbacks", []))
        resolved = []
        for c in chain:
            provider = self.providers.get(c["p"])
            if provider is not None:
                resolved.append((provider, c["m"]))
        return resolved

    def has_any_provider(self) -> bool:
        return bool(self.providers)

    async def call_agent(
        self, profile: str, agent: str, req: ChatRequest
    ) -> tuple[ChatResponse, bool]:
        """Call down the fallback chain. Returns (response, fell_back).

        Fallbacks deliberately cross vendors so a single-provider outage never
        kills the debate. Raises CouncilError only when the entire chain fails —
        the caller then degrades to a templated narration; the quant numbers
        are unaffected (narration is never on the decision path).
        """
        chain = self.resolve(profile, agent)
        if not chain:
            raise CouncilError(f"no configured provider for agent '{agent}'")
        last_err: Exception | None = None
        first_provider = chain[0][0]
        for provider, model in chain:
            if self.breaker.is_open(provider.name):
                continue
            try:
                req.model = model
                resp = await provider.complete(req)
                self.breaker.record_success(provider.name)
                return resp, (provider is not first_provider)
            except (Exception, asyncio.TimeoutError) as e:  # noqa: BLE001 — any provider error rolls to the next vendor
                last_err = e
                self.breaker.record_failure(provider.name)
        raise CouncilError(f"all providers failed for {agent}") from last_err

    async def aclose(self) -> None:
        for p in self.providers.values():
            close = getattr(p, "aclose", None)
            if close:
                await close()
