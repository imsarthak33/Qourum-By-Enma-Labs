"""The Risk Ranger — position sizing, not "sizing rules" (07_QUANT_CORE §2.5).

Stop  = Entry - k * ATR_14              (k ~ 1.5-2.0)
b     = (Target - Entry) / (Entry - Stop)
f*    = (p*b - (1-p)) / b               (Kelly)
size  = lambda * f*                     (fractional Kelly)
capped by CVaR: 95th-percentile loss must not exceed a fixed fraction of
capital. Track A has no live broker session, so the CVaR cap is computed
against a notional 100%-of-capital position by default.

The Risk Ranger's own p_bull is a volatility-conditioned neutral lean — its
real job is levels and sizing; its probability contribution is deliberately
mild and, like every agent, gets calibrated/weighted from outcomes.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from ..models import AgentFeature

TARGET_R_MULT = 2.0        # target set at 2R above entry (payoff b = 2.0)
CVAR_CAP_PCT = 0.02        # max tolerable 95% one-week loss as fraction of capital
Z_95 = 1.645


def compute(ohlcv: pd.DataFrame | None, atr_stop_k: float = 1.8) -> AgentFeature:
    agent = "risk"
    if ohlcv is None or len(ohlcv) < 30:
        return AgentFeature(agent=agent, p_bull_raw=None, ok=False,
                            error="insufficient price history")

    close = ohlcv["close"].astype(float)
    high, low = ohlcv["high"].astype(float), ohlcv["low"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr_14 = float(tr.rolling(14).mean().iloc[-1])
    entry = float(close.iloc[-1])

    stop = entry - atr_stop_k * atr_14
    target = entry + TARGET_R_MULT * atr_stop_k * atr_14
    payoff_b = (target - entry) / (entry - stop) if entry > stop else 0.0

    returns = close.pct_change().dropna()
    daily_vol = float(returns.tail(60).std())
    ann_vol = daily_vol * math.sqrt(252)

    # Mild vol-conditioned lean: elevated realized vol vs its own 1y history
    # nudges below 0.5, calm tape nudges above. Bounded to [0.35, 0.65].
    vol_1y = float(returns.std())
    vol_ratio = daily_vol / vol_1y if vol_1y > 0 else 1.0
    p_bull = float(min(max(0.5 - 0.15 * (vol_ratio - 1.0), 0.35), 0.65))

    return AgentFeature(
        agent=agent,
        p_bull_raw=round(p_bull, 4),
        features={
            "entry": round(entry, 2),
            "stop": round(stop, 2),
            "target": round(target, 2),
            "atr_14": round(atr_14, 2),
            "atr_stop_k": atr_stop_k,
            "payoff_b": round(payoff_b, 2),
            "daily_vol": round(daily_vol, 5),
            "annualised_vol": round(ann_vol, 4),
            "vol_ratio_60d_vs_1y": round(vol_ratio, 3),
        },
    )


def kelly_size(
    p_bull: float,
    entry: float,
    target: float,
    stop: float,
    kelly_lambda: float = 0.35,
    daily_vol: float = 0.02,
) -> dict[str, Any]:
    """Fractional-Kelly size with a CVaR cap (07 §2.5). Called by the Chairman
    after the final calibrated P(bull) exists — sizing uses the ensemble
    probability, not any single agent's."""
    risk = entry - stop
    if risk <= 0 or target <= entry:
        return {"kelly_fraction": None, "position_size_pct": None}
    b = (target - entry) / risk
    f_star = (p_bull * b - (1.0 - p_bull)) / b
    f_star = max(f_star, 0.0)
    size = kelly_lambda * f_star

    # CVaR-style cap: one-week 95% adverse move on the position must stay
    # under CVAR_CAP_PCT of capital.
    week_vol = daily_vol * math.sqrt(5)
    worst = Z_95 * week_vol
    cap = CVAR_CAP_PCT / worst if worst > 0 else size
    size = float(min(size, cap))

    return {
        "kelly_fraction": round(float(f_star), 4),
        "position_size_pct": round(size, 4),
        "payoff_b": round(float(b), 2),
        "cvar_cap_pct": round(float(cap), 4),
    }
