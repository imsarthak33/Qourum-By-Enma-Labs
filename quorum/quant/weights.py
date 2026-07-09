"""Online ensemble weight learning — multiplicative weights / Hedge
(07_QUANT_CORE §3.3, Freund & Schapire).

Weights converge toward whichever agent is empirically working in the current
regime, learned from realized outcomes — never hand-set, never prompted.
Cold start: uniform 1/N (07 §6).
"""

from __future__ import annotations

import math

from ..models import AGENTS


def uniform_weights(agents: tuple[str, ...] = AGENTS) -> dict[str, float]:
    return {a: 1.0 / len(agents) for a in agents}


def log_loss(p_bull: float, outcome_bull: bool, eps: float = 1e-4) -> float:
    """Per-agent log-loss on one resolved debate."""
    p = min(max(p_bull, eps), 1.0 - eps)
    return -math.log(p) if outcome_bull else -math.log(1.0 - p)


def hedge_update(
    weights: dict[str, float],
    losses: dict[str, float],
    eta: float = 0.1,
) -> dict[str, float]:
    """w_i <- w_i * exp(-eta * l_i), renormalised.

    Agents absent from `losses` (didn't participate in the resolved debate,
    e.g. an untriggered Devil's Advocate) keep their weight unchanged before
    renormalisation — they are neither rewarded nor punished.
    """
    updated = {
        a: w * math.exp(-eta * losses.get(a, 0.0)) for a, w in weights.items()
    }
    z = sum(updated.values())
    if z <= 0:
        return uniform_weights(tuple(weights.keys()))
    return {a: w / z for a, w in updated.items()}


def renormalise(weights: dict[str, float], responding: list[str]) -> dict[str, float]:
    """Degraded-path renormalisation over responding agents only
    (07 §3.2 / 02_TRD §5): a failed agent's weight is redistributed, the
    debate never aborts."""
    subset = {a: weights.get(a, 0.0) for a in responding}
    z = sum(subset.values())
    if z <= 0:
        return uniform_weights(tuple(responding))
    return {a: w / z for a, w in subset.items()}
