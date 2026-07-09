"""Narration runner: dispatches boundary-locked LLM calls, validates output,
falls back to templates. Narration is never on the decision path (07 §7) —
every failure here degrades text, never numbers.
"""

from __future__ import annotations

import math
import re
import time
from typing import Any

from ..errors import CouncilError
from ..models import AgentOpinion, Stance, stance_from_p
from ..providers import ChatRequest, Message, ProviderRegistry
from . import prompts

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _strip_tail(text: str) -> str:
    """Remove the STANCE/CONFIDENCE parse tail from displayed reasoning."""
    if "---" in text:
        head, _, tail = text.rpartition("---")
        if "STANCE" in tail:
            return head.strip()
    return text.strip()


async def narrate_agent(
    registry: ProviderRegistry,
    profile: str,
    agent: str,
    symbol: str,
    p_bull: float,
    features: dict[str, Any],
    prior_opinions: str = "",
) -> AgentOpinion:
    """Run one agent's narration call. The AgentOpinion's probability fields
    are filled by the caller — this function only produces text + telemetry."""
    if agent == "devils_advocate":
        system, user = prompts.devils_advocate_prompt(symbol, features, p_bull, prior_opinions)
    else:
        system, user = prompts.PROMPT_BUILDERS[agent](symbol, features, p_bull)

    req = ChatRequest(
        model="",  # resolved by the registry per fallback chain
        messages=[Message("system", system), Message("user", user)],
        temperature=0.4,
        max_tokens=300,
    )
    start = time.monotonic()
    try:
        resp, fell_back = await registry.call_agent(profile, agent, req)
        latency_ms = int((time.monotonic() - start) * 1000)
        return AgentOpinion(
            agent=agent,
            p_bull_raw=None, p_bull_calibrated=None, ensemble_weight=None,
            stance=None, confidence=None,
            reasoning=_strip_tail(resp.text),
            provider=resp.provider, model=resp.model,
            fell_back=fell_back,
            tokens_in=resp.tokens_in, tokens_out=resp.tokens_out,
            latency_ms=latency_ms,
        )
    except CouncilError as e:
        latency_ms = int((time.monotonic() - start) * 1000)
        return AgentOpinion(
            agent=agent,
            p_bull_raw=None, p_bull_calibrated=None, ensemble_weight=None,
            stance=None, confidence=None,
            reasoning=prompts.template_narration(agent, p_bull, features),
            narration_fallback=True,
            latency_ms=latency_ms,
            error=str(e),
        )


def _allowed_numbers(verdict_json: dict[str, Any]) -> set[str]:
    """Every numeric token the narration is allowed to mention: the verdict's
    own numbers rendered a few common ways."""
    allowed: set[str] = set()
    def add(x: Any) -> None:
        if isinstance(x, (int, float)) and x is not None and not (isinstance(x, float) and math.isnan(x)):
            for fmt in ("{:.0f}", "{:.1f}", "{:.2f}", "{:.3f}", "{:.4f}", "{}"):
                try:
                    allowed.add(fmt.format(x))
                except (ValueError, TypeError):
                    pass
            if isinstance(x, float):
                try:
                    allowed.add(str(round(x * 100, 1)))     # percentage renderings
                    allowed.add(str(int(round(x * 100))))
                except (ValueError, OverflowError):
                    pass
    for v in verdict_json.values():
        if isinstance(v, dict):
            for vv in v.values():
                add(vv)
        else:
            add(v)
    return allowed


def narration_consistent(text: str, verdict_json: dict[str, Any]) -> bool:
    """05 §6: a number-match check catches an LLM that ignored 'don't alter
    the numbers'. Any numeric token not derivable from the verdict fails."""
    allowed = _allowed_numbers(verdict_json)
    for tok in _NUM_RE.findall(text):
        norm = tok.lstrip("-")
        if norm in allowed:
            continue
        try:
            f = float(norm)
        except ValueError:
            return False
        if any(abs(f - float(a)) < 1e-6 for a in allowed if _is_float(a)):
            continue
        # small integers are fine (e.g. "5 agents", "30 days", "2:1")
        if f.is_integer() and abs(f) <= 100:
            continue
        return False
    return True


def _is_float(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


async def narrate_verdict(
    registry: ProviderRegistry,
    profile: str,
    symbol: str,
    verdict_json: dict[str, Any],
) -> tuple[str, str | None, str | None]:
    """Returns (rationale, provider, model). Falls back to a template on any
    failure or on a numbers-consistency violation (05 §6)."""
    system, user = prompts.verdict_prompt(symbol, verdict_json)
    req = ChatRequest(
        model="",
        messages=[Message("system", system), Message("user", user)],
        temperature=0.3,
        max_tokens=140,
    )
    try:
        resp, _ = await registry.call_agent(profile, "verdict_narration", req)
        text = resp.text.strip()
        words = len(text.split())
        if words <= 80 and narration_consistent(text, verdict_json):
            return text, resp.provider, resp.model
    except CouncilError:
        pass
    return prompts.template_rationale(verdict_json), None, None
