"""The Devil's Advocate — divergence/anomaly detector (07_QUANT_CORE §2.4).

Two computable triggers, not narrative contrarianism:
  D      = |P_tech - P_fund|          (model disagreement)
  Z_PCR  / Z_FIIΔ                     (positioning crowding, when data exists)

The LLM narrates WHY a fired divergence matters; the threshold test — not the
LLM — decides whether a contrarian case is statistically warranted. When the
test doesn't fire, no narration is dispatched (expected, common case —
06_WORKFLOW §9).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from ..models import AgentFeature

# Historical-top-decile placeholder for the disagreement trigger; refit from
# the local outcome log as debates accumulate (07 §2.4).
D_THRESHOLD = 0.30
Z_THRESHOLD = 2.0


def compute(
    p_tech: float | None,
    p_fund: float | None,
    p_macro: float | None,
    crowding: dict[str, float] | None = None,
) -> AgentFeature:
    """crowding: optional {'z_pcr': ..., 'z_fii_delta': ...} z-scores."""
    agent = "devils_advocate"
    crowding = crowding or {}
    z_pcr = crowding.get("z_pcr")
    z_fii = crowding.get("z_fii_delta")

    primaries = [p for p in (p_tech, p_fund, p_macro) if p is not None]
    if p_tech is None or p_fund is None:
        # Can't compute D without both sides of the disagreement.
        return AgentFeature(agent=agent, p_bull_raw=None, ok=False, triggered=False,
                            error="insufficient primary opinions for divergence test")

    d = abs(p_tech - p_fund)
    crowding_fired = any(z is not None and abs(z) > Z_THRESHOLD for z in (z_pcr, z_fii))
    triggered = d > D_THRESHOLD or crowding_fired

    features: dict[str, Any] = {
        "D": round(d, 4),
        "d_threshold": D_THRESHOLD,
        "z_pcr": z_pcr,
        "z_fii_delta": z_fii,
        "z_threshold": Z_THRESHOLD,
        "triggered_by": ("disagreement" if d > D_THRESHOLD else
                         "crowding" if crowding_fired else None),
    }

    if not triggered:
        return AgentFeature(agent=agent, p_bull_raw=None, ok=True,
                            triggered=False, features=features)

    # Seed contrarian mapping (refit from the outcome log like every other
    # agent, 07 §2.4): lean against the primary consensus, proportionally to
    # how extreme the measured divergence/crowding is.
    consensus = float(np.mean(primaries))
    strength = min(1.0, d / (2 * D_THRESHOLD))
    if crowding_fired:
        z_max = max(abs(z) for z in (z_pcr, z_fii) if z is not None)
        strength = max(strength, min(1.0, z_max / (2 * Z_THRESHOLD)))
    p_bull = 0.5 - (consensus - 0.5) * strength
    p_bull = float(min(max(p_bull, 0.02), 0.98))

    features["consensus"] = round(consensus, 4)
    features["contrarian_strength"] = round(strength, 3)
    return AgentFeature(agent=agent, p_bull_raw=round(p_bull, 4),
                        triggered=True, features=features)
