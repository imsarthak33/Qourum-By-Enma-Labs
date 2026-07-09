"""The Macro Oracle — rolling factor regression (07_QUANT_CORE §2.3).

r_i,t = alpha + b_mkt*r_mkt + b_usd*d(USDINR) + b_oil*d(oil) [+ b_fii*dFII ...]
Rolling 60-day OLS per symbol; P(bull) derives from the fitted beta vector
applied to the most recent realized macro shocks, through a sigmoid.

Factors degrade gracefully: any macro series that is missing simply drops out
of the regression, and the output flags the reduced set (00 §8 R10). FII/DII
flows and the sector-PCA rotation factor slot in as additional columns when
their adapters have data.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from ..models import AgentFeature

ROLL_WINDOW = 60
SHOCK_SCALE = 25.0  # maps a typical daily expected-return shock into sigmoid range


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def compute(
    stock_returns: pd.Series | None,
    factor_returns: dict[str, pd.Series] | None,
) -> AgentFeature:
    """stock_returns: daily pct-change series. factor_returns: name -> daily
    pct-change/diff series (e.g. market, usdinr, oil, fii, sector_pc1)."""
    agent = "macro"
    if stock_returns is None or factor_returns is None or len(stock_returns) < 30:
        return AgentFeature(agent=agent, p_bull_raw=None, ok=False,
                            error="insufficient macro/price history")

    # Align all series on shared dates; drop factors with no usable overlap.
    df = pd.DataFrame({"stock": stock_returns})
    used: list[str] = []
    for name, series in factor_returns.items():
        if series is None or series.dropna().empty:
            continue
        df[name] = series
        used.append(name)
    df = df.dropna()
    if not used or len(df) < 30:
        return AgentFeature(agent=agent, p_bull_raw=None, ok=False,
                            error="no overlapping macro factor data")

    window = df.tail(ROLL_WINDOW)
    y = window["stock"].to_numpy()
    X = np.column_stack([np.ones(len(window))] + [window[c].to_numpy() for c in used])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    alpha, betas = float(beta[0]), beta[1:]

    # Today's realized shocks = the latest observation of each factor.
    shocks = np.array([float(df[c].iloc[-1]) for c in used])
    expected_r = alpha + float(np.dot(betas, shocks))
    p_bull = _sigmoid(SHOCK_SCALE * expected_r)

    resid = y - X @ beta
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - float(np.sum(resid**2)) / ss_tot if ss_tot > 0 else 0.0

    return AgentFeature(
        agent=agent,
        p_bull_raw=round(p_bull, 4),
        features={
            "alpha": round(alpha, 6),
            "betas": {c: round(float(b), 4) for c, b in zip(used, betas)},
            "shocks": {c: round(float(s), 5) for c, s in zip(used, shocks)},
            "expected_daily_return": round(expected_r, 5),
            "r2": round(r2, 3),
            "window_days": len(window),
            "factors_used": used,
        },
    )


def sector_pc1(sector_returns: pd.DataFrame) -> pd.Series | None:
    """First principal component of sector index daily returns (07 §2.3):
    PC1 ≈ broad risk-on/risk-off. Returned as a daily factor series that can
    be fed into `compute` as another regressor."""
    clean = sector_returns.dropna()
    if clean.shape[0] < 30 or clean.shape[1] < 3:
        return None
    X = clean.to_numpy()
    X = X - X.mean(axis=0)
    _, _, vt = np.linalg.svd(X, full_matrices=False)
    pc1 = X @ vt[0]
    # Orient so that PC1 positively correlates with the average sector move.
    avg = X.mean(axis=1)
    if np.corrcoef(pc1, avg)[0, 1] < 0:
        pc1 = -pc1
    return pd.Series(pc1, index=clean.index, name="sector_pc1")
