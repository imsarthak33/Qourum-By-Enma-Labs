"""Local outcome tracking + learning loop (06_WORKFLOW §5, 07 §4).

Idempotent: for each unresolved verdict, poll price history since the verdict;
first touch of target/stop decides the result; window expiry -> expired_open.
Resolution feeds the calibration engine (isotonic refit) and the meta-learner
(Hedge weight update) — this is where the system actually learns.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

from .config import QuorumConfig
from .models import AGENTS
from .quant.calibration import fit_isotonic
from .quant.weights import hedge_update, log_loss
from .storage import Storage


def _resolve_row(row: Any, ohlcv) -> tuple[str | None, float | None, bool | None]:
    """Walk forward day by day from the verdict date; the first level touched
    wins. Returns (result, resolved_price, correct) or (None, None, None) if
    still open inside the window."""
    created = datetime.fromisoformat(row["created_at"])
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    window_end = created + timedelta(days=row["window_days"])

    action = row["action"]
    entry, target, stop = row["entry"], row["target"], row["stop"]
    if entry is None or target is None or stop is None:
        return "void", None, None

    after = ohlcv[ohlcv.index > created.replace(tzinfo=None)]
    for ts, day in after.iterrows():
        hi, lo, close = float(day["high"]), float(day["low"]), float(day["close"])
        if action == "BUY":
            hit_target, hit_stop = hi >= target, lo <= stop
        else:  # SELL: mirrored levels — target below entry, stop above
            hit_target, hit_stop = lo <= target, hi >= stop
        if hit_target and hit_stop:
            # Both touched in one bar: unknowable ordering on dailies — treat
            # conservatively as stop-first.
            return "stop_hit", close, False
        if hit_target:
            return "target_hit", close, True
        if hit_stop:
            return "stop_hit", close, False
        if ts.to_pydatetime().replace(tzinfo=timezone.utc) > window_end:
            break

    if datetime.now(timezone.utc) > window_end:
        return "expired_open", float(after["close"].iloc[-1]) if len(after) else None, False
    return None, None, None


def check_outcomes(storage: Storage, config: QuorumConfig) -> list[dict[str, Any]]:
    """Run one outcome-tracking pass. Returns a summary of what resolved."""
    from .data import fetch_ohlcv

    resolved_now: list[dict[str, Any]] = []
    for row in storage.pending_outcomes():
        ohlcv = fetch_ohlcv(row["symbol"], row["exchange"], period="3mo")
        if ohlcv is None:
            storage.touch_outcome(row["verdict_debate_id"])
            continue
        result, price, correct = _resolve_row(row, ohlcv)
        if result is None:
            storage.touch_outcome(row["verdict_debate_id"])
            continue
        storage.resolve_outcome(row["verdict_debate_id"], result, price, correct)
        resolved_now.append({
            "debate_id": row["verdict_debate_id"], "symbol": row["symbol"],
            "result": result, "correct": correct, "share": bool(row["share"]),
            "row": dict(row),
        })
        if correct is not None:
            meta_learner_update(storage, config, row["verdict_debate_id"], bool(correct))
    return resolved_now


def meta_learner_update(
    storage: Storage, config: QuorumConfig, debate_id: str, outcome_bull_correct: bool
) -> dict[str, float]:
    """Per-resolved-debate Hedge update (07 §3.3): each participating agent is
    charged its log-loss on the realized outcome."""
    p_cals = storage.calibrated_p_for_debate(debate_id)
    if not p_cals:
        return storage.latest_weights()
    losses = {a: log_loss(p, outcome_bull_correct) for a, p in p_cals.items()}
    new_weights = hedge_update(storage.latest_weights(), losses, eta=config.hedge_eta)
    storage.save_weights(new_weights, config.hedge_eta, debate_id)
    return new_weights


def calibration_refit(storage: Storage, version: str | None = None) -> dict[str, int]:
    """Weekly isotonic refit per agent from the local outcome log (07 §4).
    Below the minimum sample count the identity seed is kept (07 §6)."""
    version = version or f"v{datetime.now(timezone.utc):%Y%m%d}"
    fitted: dict[str, int] = {}
    for agent in AGENTS:
        pairs = storage.resolved_outcomes_for_agent(agent)
        if not pairs:
            continue
        p_raw = np.array([p for p, _ in pairs])
        y = np.array([o for _, o in pairs], dtype=float)
        curve = fit_isotonic(agent, p_raw, y, version=version)
        storage.save_curve(curve)
        fitted[agent] = curve.n_samples
    return fitted
