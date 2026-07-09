"""The Technician — regime-conditioned signal model (07_QUANT_CORE §2.1).

2-state Gaussian HMM on daily returns (rolling 252d window) infers a
trend/reversion regime; a mean-reversion z-score (price vs SMA20, in ATR
units) is sign-flipped by regime and mapped to P(bull) through a logistic.

hmmlearn is optional: without it (or with too little data) the regime falls
back to an autocorrelation heuristic and the output is flagged so the verdict
can carry a reduced-confidence caveat (00 §8 R10 pattern).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from ..models import AgentFeature

# Logistic coefficients — seeds, refit via logistic regression against the
# outcome log once enough outcomes resolve (07 §2.1 "fit, not hand-tuned";
# §6 cold start applies until then).
B0, B1, B2 = 0.0, 0.8, 0.25

HMM_WINDOW = 252


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _fit_hmm_regime(returns: np.ndarray) -> tuple[str, str]:
    """Returns (regime, method). Regime in {trend, reversion}."""
    try:
        from hmmlearn.hmm import GaussianHMM

        X = returns.reshape(-1, 1)
        hmm = GaussianHMM(n_components=2, covariance_type="diag", n_iter=100, random_state=7)
        hmm.fit(X)
        states = hmm.predict(X)  # Viterbi path
        current = states[-1]
        # Label: the state with larger |mean| daily drift is the trending one.
        means = hmm.means_.flatten()
        trend_state = int(np.argmax(np.abs(means)))
        return ("trend" if current == trend_state else "reversion", "hmm")
    except Exception:  # noqa: BLE001 — hmmlearn missing or fit failed; degrade, don't die
        # Heuristic fallback: positive lag-1 autocorrelation of returns ≈ trending.
        if len(returns) > 20:
            ac = float(np.corrcoef(returns[:-1], returns[1:])[0, 1])
            return ("trend" if ac > 0 else "reversion", "autocorr_fallback")
        return ("reversion", "insufficient_data")


def compute(ohlcv: pd.DataFrame | None) -> AgentFeature:
    """ohlcv: DataFrame with columns open/high/low/close/volume, daily rows."""
    agent = "technician"
    if ohlcv is None or len(ohlcv) < 30:
        return AgentFeature(agent=agent, p_bull_raw=None, ok=False,
                            error="insufficient price history")

    df = ohlcv.tail(HMM_WINDOW).copy()
    close = df["close"].astype(float)
    high, low = df["high"].astype(float), df["low"].astype(float)
    volume = df["volume"].astype(float)

    returns = close.pct_change().dropna().to_numpy()
    regime, regime_method = _fit_hmm_regime(returns)

    sma_20 = float(close.rolling(20).mean().iloc[-1])
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr_14 = float(tr.rolling(14).mean().iloc[-1])
    last = float(close.iloc[-1])

    z_tech = (last - sma_20) / atr_14 if atr_14 > 0 else 0.0
    # Regime flips the read (07 §2.1): trend => momentum continuation
    # (above SMA is bullish); reversion => fade the move.
    z_signed = z_tech if regime == "trend" else -z_tech

    vol_mean = float(volume.rolling(20).mean().iloc[-1])
    vol_std = float(volume.rolling(20).std().iloc[-1])
    volume_z = (float(volume.iloc[-1]) - vol_mean) / vol_std if vol_std > 0 else 0.0
    # Volume confirms the signed signal rather than carrying direction itself.
    vol_term = volume_z * math.copysign(1.0, z_signed) if z_signed != 0 else 0.0

    p_bull = _sigmoid(B0 + B1 * z_signed + B2 * vol_term)

    return AgentFeature(
        agent=agent,
        p_bull_raw=round(p_bull, 4),
        features={
            "regime": regime,
            "regime_method": regime_method,
            "z_tech": round(z_tech, 3),
            "z_signed": round(z_signed, 3),
            "volume_z": round(volume_z, 3),
            "sma_20": round(sma_20, 2),
            "atr_14": round(atr_14, 2),
            "last": round(last, 2),
        },
    )
