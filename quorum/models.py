"""Domain models shared across the pipeline.

Design rule (07_QUANT_CORE §1): the Decision Layer is pure math over these
structures. Every numeric field a user sees is computed, never LLM-authored;
the only LLM-generated content is narration text.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

AGENTS = ("technician", "fundamentalist", "macro", "devils_advocate", "risk")

DISCLAIMER = "AI analysis, not investment advice."


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    WAIT = "WAIT"
    AVOID = "AVOID"
    NO_CALL = "NO_CALL"


class Stance(str, Enum):
    BULL = "BULL"
    BEAR = "BEAR"
    NEUTRAL = "NEUTRAL"


def stance_from_p(p_bull: float) -> Stance:
    """Stance is derived from the calibrated probability, never chosen by an LLM."""
    if p_bull >= 0.55:
        return Stance.BULL
    if p_bull <= 0.45:
        return Stance.BEAR
    return Stance.NEUTRAL


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class FactPack:
    """Snapshot of everything known at query time, with per-field provenance.

    Adapters degrade gracefully: a missing source appears in `sources` with a
    `missing`/`stale` flag instead of raising (02_TRD §6).
    """

    symbol: str
    exchange: str = "NSE"
    captured_at: datetime = field(default_factory=utcnow)
    # Technician / Risk inputs
    price: dict[str, Any] = field(default_factory=dict)      # last, sma_20, atr_14, vol z, ohlcv summary
    # Fundamentalist inputs
    fundamentals: dict[str, Any] = field(default_factory=dict)
    # Macro Oracle inputs
    macro: dict[str, Any] = field(default_factory=dict)      # factor shocks, betas inputs
    flows: dict[str, Any] = field(default_factory=dict)      # FII/DII (often missing on free sources)
    catalysts: dict[str, Any] = field(default_factory=dict)  # earnings dates, corporate actions
    sources: dict[str, str] = field(default_factory=dict)    # key -> "ok" | "stale" | "missing"

    def to_json(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "exchange": self.exchange,
            "captured_at": self.captured_at.isoformat(),
            "price": self.price,
            "fundamentals": self.fundamentals,
            "macro": self.macro,
            "flows": self.flows,
            "catalysts": self.catalysts,
            "sources": self.sources,
        }


@dataclass
class AgentFeature:
    """Output of one agent's deterministic quant feature model (07 §2)."""

    agent: str
    p_bull_raw: float | None            # None => the feature model itself failed
    features: dict[str, Any] = field(default_factory=dict)
    ok: bool = True
    error: str | None = None
    triggered: bool = True              # devils_advocate: did the threshold test fire?


@dataclass
class AgentOpinion:
    """One agent's full contribution: computed probability + narration."""

    agent: str
    p_bull_raw: float | None
    p_bull_calibrated: float | None
    ensemble_weight: float | None
    stance: Stance | None
    confidence: int | None              # = round(100 * p_bull_calibrated)
    reasoning: str = ""
    features: dict[str, Any] = field(default_factory=dict)
    info_boundary: list[str] = field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    triggered: bool = True
    fell_back: bool = False
    narration_fallback: bool = False    # templated narration used
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    error: str | None = None


@dataclass
class Verdict:
    """Output contract of the deterministic Chairman (07 §3.5).

    `rationale` is the ONLY LLM-generated field. Everything else is computed.
    """

    action: Action
    entry: float | None
    target: float | None
    stop: float | None
    risk_reward: float | None
    p_bull_calibrated: float
    expected_value: float
    edge: float
    hurdle_tau: float
    kelly_fraction: float | None
    position_size_pct: float | None
    agent_weights: dict[str, float]
    calibration_confidence: str         # "low" | "medium" | "high" (07 §6 cold start)
    rationale: str = ""
    narration_provider: str | None = None
    narration_model: str | None = None
    disclaimer: str = DISCLAIMER

    def to_json(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "entry": self.entry,
            "target": self.target,
            "stop": self.stop,
            "risk_reward": self.risk_reward,
            "p_bull_calibrated": round(self.p_bull_calibrated, 4),
            "expected_value": round(self.expected_value, 4),
            "edge": round(self.edge, 4),
            "hurdle_tau": self.hurdle_tau,
            "kelly_fraction": self.kelly_fraction,
            "position_size_pct": self.position_size_pct,
            "agent_weights": {k: round(v, 4) for k, v in self.agent_weights.items()},
            "calibration_confidence": self.calibration_confidence,
            "rationale": self.rationale,
            "disclaimer": self.disclaimer,
        }


@dataclass
class DebateResult:
    """The aggregate root: one full council run (03_DATABASE §2)."""

    debate_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    symbol: str = ""
    exchange: str = "NSE"
    query: str = ""
    provider_profile: str = "free"
    status: str = "running"             # running | complete | degraded | failed
    degraded: bool = False
    fact_pack: FactPack | None = None
    quant_features: dict[str, Any] = field(default_factory=dict)
    calibration_version: str = "identity-seed"
    opinions: list[AgentOpinion] = field(default_factory=list)
    verdict: Verdict | None = None
    latency_ms: int = 0
    created_at: datetime = field(default_factory=utcnow)
    share: bool = False                 # opt-in leaderboard submission on resolution
