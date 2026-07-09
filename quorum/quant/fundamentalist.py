"""The Fundamentalist — cross-sectional multi-factor model (07_QUANT_CORE §2.2).

F = {Z_value, Z_quality, Z_growth, Z_momentum, delta_qual}; S = w^T F;
P(bull) = sigmoid(S). Factor weights `w` are meant to come from a monthly
Fama-MacBeth cross-sectional regression over the NSE-500 universe; until a
universe snapshot is available locally, seeded weights are used and the
output flags the reduced fit (00 §8 R10: degrade to a reduced factor set,
flag reduced confidence, never silently proceed).

`delta_qual` is the one LLM-derived input (filings/concall sentiment in
[-1, 1]) — treated as a fifth factor, not as the model (07 §2.2). It is
passed in by the orchestrator; 0.0 when unavailable.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ..models import AgentFeature

# Seed factor weights — replaced by Fama-MacBeth estimates once a factor
# universe snapshot exists (03_DATABASE §3.15-3.16).
SEED_WEIGHTS = {
    "z_value": 0.25,
    "z_quality": 0.25,
    "z_growth": 0.20,
    "z_momentum": 0.20,
    "delta_qual": 0.10,
}

# Fallback dispersion priors used to standardize raw ratios when no
# cross-sectional universe is available (rough NSE large/mid-cap scales).
PRIORS = {
    "pe": (25.0, 15.0),          # (mean, std)
    "pb": (3.5, 2.5),
    "roe": (0.14, 0.08),
    "debt_to_equity": (0.8, 0.7),
    "revenue_cagr": (0.10, 0.10),
    "eps_growth": (0.10, 0.15),
    "mom_12_1": (0.12, 0.25),
}


def _z(value: float | None, key: str, invert: bool = False) -> float | None:
    if value is None:
        return None
    mu, sigma = PRIORS[key]
    z = (float(value) - mu) / sigma
    z = max(min(z, 3.0), -3.0)
    return -z if invert else z


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def compute(
    fundamentals: dict[str, Any] | None,
    delta_qual: float = 0.0,
    factor_weights: dict[str, float] | None = None,
) -> AgentFeature:
    """fundamentals keys (any may be missing): pe, pb, roe, debt_to_equity,
    revenue_cagr, eps_growth, mom_12_1, sector."""
    agent = "fundamentalist"
    f = fundamentals or {}

    # Value: cheap vs prior => bullish (inverse P/E, P/B).
    z_val_parts = [x for x in (_z(f.get("pe"), "pe", invert=True),
                               _z(f.get("pb"), "pb", invert=True)) if x is not None]
    z_value = float(np.mean(z_val_parts)) if z_val_parts else None

    # Quality: high ROE, low leverage.
    z_q_parts = [x for x in (_z(f.get("roe"), "roe"),
                             _z(f.get("debt_to_equity"), "debt_to_equity", invert=True))
                 if x is not None]
    z_quality = float(np.mean(z_q_parts)) if z_q_parts else None

    # Growth: revenue / EPS CAGR.
    z_g_parts = [x for x in (_z(f.get("revenue_cagr"), "revenue_cagr"),
                             _z(f.get("eps_growth"), "eps_growth")) if x is not None]
    z_growth = float(np.mean(z_g_parts)) if z_g_parts else None

    # Momentum: 12-1 month return (short-term reversal excluded upstream).
    z_momentum = _z(f.get("mom_12_1"), "mom_12_1")

    factors = {
        "z_value": z_value,
        "z_quality": z_quality,
        "z_growth": z_growth,
        "z_momentum": z_momentum,
        "delta_qual": float(max(min(delta_qual, 1.0), -1.0)),
    }
    available = {k: v for k, v in factors.items() if v is not None}
    if len([k for k in available if k != "delta_qual"]) == 0:
        return AgentFeature(agent=agent, p_bull_raw=None, ok=False,
                            error="no fundamental data available")

    # Renormalise weights over the factors we actually have (reduced factor set).
    weights = dict(factor_weights or SEED_WEIGHTS)
    w = {k: weights.get(k, 0.0) for k in available}
    z_sum = sum(w.values())
    w = {k: v / z_sum for k, v in w.items()} if z_sum > 0 else w

    score = sum(w[k] * available[k] for k in available)
    p_bull = _sigmoid(score)

    reduced = len(available) < len(factors)
    return AgentFeature(
        agent=agent,
        p_bull_raw=round(p_bull, 4),
        features={
            **{k: (round(v, 3) if v is not None else None) for k, v in factors.items()},
            "score": round(score, 3),
            "weights": {k: round(v, 3) for k, v in w.items()},
            "weight_source": "fama_macbeth" if factor_weights else "seed",
            "reduced_factor_set": reduced,
            "sector": f.get("sector"),
        },
    )


def fama_macbeth_weights(
    factor_panel: "np.ndarray", forward_returns: "np.ndarray", factor_names: list[str]
) -> dict[str, float]:
    """Monthly Fama-MacBeth refit (07 §2.2): cross-sectional OLS of forward
    1-month returns on lagged factor z-scores, per period, then the
    time-series mean of the per-period coefficient vectors.

    factor_panel: shape (n_periods, n_stocks, n_factors);
    forward_returns: shape (n_periods, n_stocks).
    """
    coefs = []
    for t in range(factor_panel.shape[0]):
        X = factor_panel[t]
        y = forward_returns[t]
        mask = ~(np.isnan(X).any(axis=1) | np.isnan(y))
        if mask.sum() < X.shape[1] + 2:
            continue
        Xt = np.column_stack([np.ones(mask.sum()), X[mask]])
        beta, *_ = np.linalg.lstsq(Xt, y[mask], rcond=None)
        coefs.append(beta[1:])
    if not coefs:
        return dict(SEED_WEIGHTS)
    mean_coefs = np.mean(coefs, axis=0)
    total = np.sum(np.abs(mean_coefs))
    if total == 0:
        return dict(SEED_WEIGHTS)
    return {name: float(c / total) for name, c in zip(factor_names, mean_coefs)}
