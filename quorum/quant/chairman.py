"""The Chairman — deterministic decision pipeline (07_QUANT_CORE §3).

NOT an LLM call. A pure function:
    chairman(features, curves, weights, levels) -> Verdict
calibrate -> logarithmic opinion pool -> Hedge weights -> EV/hurdle decision.
Same inputs, same outputs, every time; fully unit-testable; zero token cost.

The `rationale` field is filled in later by a narration call that explains —
and may never alter — the numbers computed here.
"""

from __future__ import annotations

import math
from typing import Any

from ..models import Action, AgentFeature, Verdict
from .calibration import CalibrationCurve, clamp_p
from .risk import kelly_size
from .weights import renormalise, uniform_weights


def log_opinion_pool(p_hats: dict[str, float], weights: dict[str, float]) -> float:
    """P(bull) = Π p_i^w_i / (Π p_i^w_i + Π (1-p_i)^w_i)   (07 §3.2).

    Multiplicatively punishes confident-and-wrong experts — the correct
    behaviour when one agent is systematically miscalibrated in a regime.
    Computed in log space for numerical stability.
    """
    if not p_hats:
        return 0.5
    log_bull = sum(weights[a] * math.log(clamp_p(p)) for a, p in p_hats.items())
    log_bear = sum(weights[a] * math.log(1.0 - clamp_p(p)) for a, p in p_hats.items())
    m = max(log_bull, log_bear)
    num = math.exp(log_bull - m)
    den = num + math.exp(log_bear - m)
    return num / den


def decide_action(edge: float, tau: float, degraded: bool) -> Action:
    """EV-against-hurdle decision rule (07 §3.4), with the PRD's explicit
    NO_CALL credibility state (01_PRD §10): below the hurdle the system says
    the edge doesn't clear uncertainty and cost, rather than manufacturing a
    confident-sounding call.
    """
    if edge > tau:
        return Action.AVOID if degraded else (Action.BUY if edge > 0 else Action.NO_CALL)
    if edge > tau / 2:
        return Action.WAIT
    return Action.NO_CALL


def synthesize(
    features: dict[str, AgentFeature],
    curves: dict[str, CalibrationCurve],
    weights: dict[str, float] | None,
    hurdle_tau: float = 0.15,
    kelly_lambda: float = 0.35,
) -> tuple[Verdict, dict[str, float], bool]:
    """Returns (verdict, calibrated_p per agent, degraded).

    `features` — every agent's AgentFeature (an untriggered Devil's Advocate
    or a failed model has p_bull_raw=None and is excluded from aggregation;
    weights renormalise over responding agents, 07 §3.2 degraded path).
    """
    weights = weights or uniform_weights()

    # 1. Calibrate each responding agent's raw probability (07 §3.1).
    p_hats: dict[str, float] = {}
    for agent, feat in features.items():
        if feat.p_bull_raw is None:
            continue
        curve = curves.get(agent) or CalibrationCurve.identity(agent)
        p_hats[agent] = curve.apply(feat.p_bull_raw)

    responding = list(p_hats.keys())
    # Devil's Advocate not triggering is the expected common case, not a
    # failure — degraded means a *primary* model failed (06_WORKFLOW §9).
    primary_agents = {"technician", "fundamentalist", "macro", "risk"}
    failed_primaries = [
        a for a in primary_agents
        if a in features and features[a].p_bull_raw is None
    ]
    degraded = len(failed_primaries) > 0 or len(responding) < 3

    # 2. Renormalise Hedge weights over responding agents, aggregate (07 §3.2-3.3).
    w = renormalise(weights, responding) if responding else {}
    p_bull = log_opinion_pool(p_hats, w) if responding else 0.5

    # 3. Levels come from the Risk Ranger's ATR model (07 §2.5).
    risk_feat = features.get("risk")
    levels = risk_feat.features if (risk_feat and risk_feat.ok) else {}
    entry = levels.get("entry")
    stop = levels.get("stop")
    target = levels.get("target")
    daily_vol = levels.get("daily_vol", 0.02)

    # 4. EV against the hurdle (07 §3.4). Sign-adjusted: if the pool leans
    # bear, evaluate the short side with mirrored levels.
    #
    # Deviation from the raw 07 §3.4 formula, deliberately: edge = EV/risk is
    # satisfiable by construction — with an asymmetric 2R target, a coin-flip
    # P=0.5 already scores edge 0.5 and would clear any tau in the 0.15-0.2
    # range on zero information. The hurdle must test *informational* edge,
    # so we subtract the neutral-prior baseline (what P=0.5 would score on
    # the same levels): edge_net = edge(p) - edge(0.5).
    direction_bull = p_bull >= 0.5
    if entry is not None and stop is not None and target is not None and entry > stop:
        if direction_bull:
            p_dir, tgt, stp = p_bull, target, stop
        else:
            p_dir = 1.0 - p_bull
            tgt = entry - (target - entry)   # mirror target below entry
            stp = entry + (entry - stop)     # mirror stop above entry
        reward = abs(tgt - entry)
        risk_amt = abs(entry - stp)
        ev = p_dir * reward - (1.0 - p_dir) * risk_amt
        if risk_amt > 0:
            edge_raw = ev / risk_amt
            edge_neutral = (0.5 * reward - 0.5 * risk_amt) / risk_amt
            edge = edge_raw - edge_neutral
            rr = reward / risk_amt
        else:
            edge, rr = 0.0, None
    else:
        ev, edge, rr = 0.0, 0.0, None
        tgt, stp = target, stop
        degraded = True

    action = decide_action(edge, hurdle_tau, degraded)
    if action == Action.BUY and not direction_bull:
        action = Action.SELL

    # 5. Fractional-Kelly sizing on the ensemble probability (07 §2.5) —
    # only when there's an actionable call.
    if action in (Action.BUY, Action.SELL) and entry is not None:
        p_for_size = p_bull if direction_bull else 1.0 - p_bull
        e_dir, t_dir, s_dir = (
            (entry, target, stop) if direction_bull
            else (entry, entry + (entry - stop), entry - (target - entry))
        )
        sizing = kelly_size(p_for_size, e_dir, t_dir, s_dir,
                            kelly_lambda=kelly_lambda, daily_vol=daily_vol)
    else:
        sizing = {"kelly_fraction": None, "position_size_pct": None}

    # 6. Cold-start transparency (07 §6): report the weakest calibration
    # confidence among responding agents.
    order = {"low": 0, "medium": 1, "high": 2}
    confidences = [
        (curves.get(a) or CalibrationCurve.identity(a)).confidence for a in responding
    ]
    calib_conf = min(confidences, key=lambda c: order[c]) if confidences else "low"

    verdict = Verdict(
        action=action,
        entry=round(entry, 2) if entry is not None else None,
        target=round(tgt, 2) if tgt is not None else None,
        stop=round(stp, 2) if stp is not None else None,
        risk_reward=round(rr, 2) if rr is not None else None,
        p_bull_calibrated=round(p_bull, 4),
        expected_value=round(ev, 4),
        edge=round(edge, 4),
        hurdle_tau=hurdle_tau,
        kelly_fraction=sizing.get("kelly_fraction"),
        position_size_pct=sizing.get("position_size_pct"),
        agent_weights={a: round(w.get(a, 0.0), 4) for a in responding},
        calibration_confidence=calib_conf,
    )
    return verdict, p_hats, degraded


def validate_levels(verdict: Verdict, fact_pack_price: dict[str, Any]) -> bool:
    """Guard against a stale/corrupt fact pack (05 §6): entry/target/stop must
    fall within a sane band around the observed 52w range."""
    lo = fact_pack_price.get("low_52w")
    hi = fact_pack_price.get("high_52w")
    if lo is None or hi is None:
        return True  # nothing to validate against; sources flag staleness separately
    band_lo, band_hi = lo * 0.5, hi * 1.5
    for v in (verdict.entry, verdict.target, verdict.stop):
        if v is not None and not (band_lo <= v <= band_hi):
            return False
    return True
