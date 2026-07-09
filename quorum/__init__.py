"""Quorum — don't trade alone.

An open-source, auditable, provider-agnostic AI debate engine for stock
analysis. Five role-locked agents, each backed by a real statistical model;
a deterministic Chairman — pure math, zero token cost — issues the verdict.
Math decides, LLMs narrate.

Library usage (01_PRD §5.1):

    import asyncio
    import quorum

    result = asyncio.run(quorum.analyze("TATAMOTORS"))
    print(result.verdict.to_json())
"""

from __future__ import annotations

__version__ = "0.1.0"

from .config import QuorumConfig
from .models import (
    Action,
    AgentOpinion,
    DebateResult,
    FactPack,
    Stance,
    Verdict,
)


async def analyze(
    symbol: str,
    query: str = "",
    exchange: str = "NSE",
    share: bool = False,
    config: "QuorumConfig | None" = None,
    on_event=None,
) -> DebateResult:
    """Run a full council debate for `symbol`. The library entrypoint —
    equivalent to `quorum analyze SYMBOL` on the CLI."""
    from .orchestrator import run_debate

    kwargs = {}
    if on_event is not None:
        kwargs["on_event"] = on_event
    return await run_debate(
        symbol, query=query, exchange=exchange, share=share, config=config, **kwargs
    )


__all__ = [
    "analyze",
    "QuorumConfig",
    "DebateResult",
    "Verdict",
    "AgentOpinion",
    "FactPack",
    "Action",
    "Stance",
    "__version__",
]
