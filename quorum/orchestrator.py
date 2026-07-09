"""Orchestration (02_TRD §5): enrich -> brief -> compute -> narrate (fan-out)
-> Devil's Advocate check -> synthesise (deterministic) -> narrate verdict ->
persist.

The quant feature models resolve BEFORE any narration is dispatched
(`feature_ready` fires first), so probabilities are populated immediately and
narration is a progressive enhancement, never the source of truth. Partial
failure never aborts the debate; if every narration provider is down the full
verdict still computes — numbers only, explanations unavailable
(06_WORKFLOW §9).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

from .agents import narration, prompts
from .config import QuorumConfig
from .models import (
    AgentFeature,
    AgentOpinion,
    DebateResult,
    FactPack,
    stance_from_p,
)
from .providers import ProviderRegistry
from .quant import chairman, devils_advocate, fundamentalist, macro, risk, technician
from .storage import Storage

EventCallback = Callable[[dict[str, Any]], None]

PRIMARY_AGENTS = ("technician", "fundamentalist", "macro", "risk")


def _noop(_: dict[str, Any]) -> None:
    pass


async def run_debate(
    symbol: str,
    query: str = "",
    exchange: str = "NSE",
    config: QuorumConfig | None = None,
    registry: ProviderRegistry | None = None,
    storage: Storage | None = None,
    on_event: EventCallback = _noop,
    share: bool = False,
    fact_pack_override: tuple[FactPack, dict[str, Any]] | None = None,
) -> DebateResult:
    """Run one full council debate. Library entrypoint (01_PRD §5.1: must be
    usable as an importable library, not just a CLI).

    `fact_pack_override` lets tests and notebooks inject a fixed fact pack
    (golden debates, 05 §9) instead of hitting the network.
    """
    config = config or QuorumConfig.load()
    registry = registry or config.build_registry()
    storage = storage or Storage(config.db_path)
    started = time.monotonic()

    debate = DebateResult(symbol=symbol.upper(), exchange=exchange, query=query,
                          provider_profile=config.profile, share=share)
    on_event({"event": "debate_start", "debate_id": debate.debate_id, "symbol": debate.symbol})

    # 1. Enrich — fact pack + raw frames (data adapters degrade, never raise).
    if fact_pack_override is not None:
        fact_pack, raw = fact_pack_override
    else:
        from .data import build_fact_pack
        fact_pack, raw = await asyncio.to_thread(build_fact_pack, debate.symbol, exchange)
    debate.fact_pack = fact_pack
    on_event({"event": "fact_pack", "sources": fact_pack.sources})

    if fact_pack.sources.get("price") == "missing":
        # Without prices nothing downstream can run (06_WORKFLOW §2: Failed).
        debate.status = "failed"
        debate.latency_ms = int((time.monotonic() - started) * 1000)
        storage.save_debate(debate)
        on_event({"event": "error", "message": f"no price data for {debate.symbol}"})
        return debate

    # 2-3. Compute — deterministic feature models, concurrent, no provider
    # dependency (07 §2). Milliseconds, so feature_ready fires ~immediately.
    ohlcv = raw.get("ohlcv")
    features: dict[str, AgentFeature] = {}

    async def _compute(agent: str) -> AgentFeature:
        if agent == "technician":
            return await asyncio.to_thread(technician.compute, ohlcv)
        if agent == "fundamentalist":
            return await asyncio.to_thread(
                fundamentalist.compute, fact_pack.fundamentals or None
            )
        if agent == "macro":
            return await asyncio.to_thread(
                macro.compute, raw.get("stock_returns"), raw.get("macro_factors")
            )
        return await asyncio.to_thread(risk.compute, ohlcv, config.atr_stop_k)

    results = await asyncio.gather(*(_compute(a) for a in PRIMARY_AGENTS))
    curves = storage.latest_curves()
    for feat in results:
        features[feat.agent] = feat
        p_cal = curves[feat.agent].apply(feat.p_bull_raw) if feat.p_bull_raw is not None else None
        on_event({
            "event": "feature_ready", "agent": feat.agent,
            "p_bull": feat.p_bull_raw, "p_bull_calibrated": p_cal,
            "ok": feat.ok, "features": feat.features, "error": feat.error,
        })

    # 6 (ordering per 05 §4): Devil's Advocate divergence/crowding test runs
    # once the primary probabilities exist; narration only if it fires.
    da = devils_advocate.compute(
        p_tech=features["technician"].p_bull_raw,
        p_fund=features["fundamentalist"].p_bull_raw,
        p_macro=features["macro"].p_bull_raw,
        crowding=None,  # PCR/FII z-scores: no free source yet (reduced set)
    )
    features["devils_advocate"] = da
    on_event({"event": "feature_ready", "agent": "devils_advocate",
              "p_bull": da.p_bull_raw, "triggered": da.triggered,
              "features": da.features})

    # 4. Narrate — concurrent fan-out, cross-vendor fallback; templated text
    # if a whole chain fails. Numbers are already locked in.
    narrating = [a for a in PRIMARY_AGENTS if features[a].p_bull_raw is not None]

    async def _narrate(agent: str) -> AgentOpinion:
        feat = features[agent]
        p = feat.p_bull_raw or 0.5
        on_event({"event": "agent_start", "agent": agent})
        if registry.has_any_provider():
            op = await narration.narrate_agent(
                registry, config.profile, agent, debate.symbol, p, feat.features
            )
        else:
            op = AgentOpinion(
                agent=agent, p_bull_raw=None, p_bull_calibrated=None,
                ensemble_weight=None, stance=None, confidence=None,
                reasoning=prompts.template_narration(agent, p, feat.features),
                narration_fallback=True,
            )
        return op

    opinions = {op.agent: op for op in await asyncio.gather(*(_narrate(a) for a in narrating))}

    if da.triggered and da.p_bull_raw is not None:
        on_event({"event": "agent_start", "agent": "devils_advocate"})
        prior_text = "\n".join(
            f"- {a}: P(bull)={features[a].p_bull_raw}" for a in ("technician", "fundamentalist", "macro")
            if features[a].p_bull_raw is not None
        )
        if registry.has_any_provider():
            opinions["devils_advocate"] = await narration.narrate_agent(
                registry, config.profile, "devils_advocate", debate.symbol,
                da.p_bull_raw, da.features, prior_opinions=prior_text,
            )
        else:
            opinions["devils_advocate"] = AgentOpinion(
                agent="devils_advocate", p_bull_raw=None, p_bull_calibrated=None,
                ensemble_weight=None, stance=None, confidence=None,
                reasoning=prompts.template_narration("devils_advocate", da.p_bull_raw, da.features),
                narration_fallback=True,
            )

    # 7. Synthesise — the deterministic Chairman (07 §3). Pure function call.
    weights = storage.latest_weights()
    verdict, p_hats, degraded = chairman.synthesize(
        features, curves, weights,
        hurdle_tau=config.hurdle_tau, kelly_lambda=config.kelly_lambda,
    )
    if not chairman.validate_levels(verdict, fact_pack.price):
        degraded = True
        on_event({"event": "warning",
                  "message": "levels outside sane range vs 52w band — fact pack may be stale"})

    # Finalize opinions with calibrated numbers, stances, weights, boundaries.
    final_opinions: list[AgentOpinion] = []
    for agent, feat in features.items():
        op = opinions.get(agent)
        if op is None:
            if agent == "devils_advocate" and not feat.triggered:
                continue  # untriggered DA: expected, nothing to record
            op = AgentOpinion(agent=agent, p_bull_raw=None, p_bull_calibrated=None,
                              ensemble_weight=None, stance=None, confidence=None,
                              reasoning="", error=feat.error, narration_fallback=True)
        op.p_bull_raw = feat.p_bull_raw
        op.p_bull_calibrated = p_hats.get(agent)
        op.ensemble_weight = verdict.agent_weights.get(agent)
        op.triggered = feat.triggered
        op.features = feat.features
        op.info_boundary = prompts.INFO_BOUNDARIES.get(agent, [])
        if op.p_bull_calibrated is not None:
            op.stance = stance_from_p(op.p_bull_calibrated)
            op.confidence = round(100 * op.p_bull_calibrated)
        final_opinions.append(op)
        on_event({"event": "agent_done", "agent": agent,
                  "stance": op.stance.value if op.stance else None,
                  "confidence": op.confidence, "reasoning": op.reasoning,
                  "fallback": op.narration_fallback})

    bull = round(100 * verdict.p_bull_calibrated)
    on_event({"event": "sentiment", "bull": bull, "bear": 100 - bull})

    # 8. Narrate verdict — the ONLY LLM text in the verdict; validated against
    # the computed numbers, templated on mismatch (05 §6).
    vjson = verdict.to_json()
    if registry.has_any_provider():
        rationale, nprov, nmodel = await narration.narrate_verdict(
            registry, config.profile, debate.symbol, vjson
        )
    else:
        rationale, nprov, nmodel = prompts.template_rationale(vjson), None, None
    verdict.rationale = rationale
    verdict.narration_provider = nprov
    verdict.narration_model = nmodel

    debate.opinions = final_opinions
    debate.verdict = verdict
    debate.degraded = degraded
    debate.status = "degraded" if degraded else "complete"
    debate.quant_features = {a: f.features for a, f in features.items()}
    debate.calibration_version = next(iter(curves.values())).version if curves else "identity-seed"
    debate.latency_ms = int((time.monotonic() - started) * 1000)

    on_event({"event": "chairman", **verdict.to_json()})

    # 9. Persist — full reproducibility snapshot (02_TRD §5: a verdict must be
    # re-derivable from its stored inputs).
    storage.save_debate(debate)
    on_event({"event": "done", "debate_id": debate.debate_id,
              "degraded": degraded, "latency_ms": debate.latency_ms})
    return debate
