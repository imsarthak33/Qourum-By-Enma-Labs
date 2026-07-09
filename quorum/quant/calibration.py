"""Isotonic calibration of agent probabilities (07_QUANT_CORE §3.1, §6).

Each agent's raw model probability is mapped through a monotonic curve fit
against realized outcomes. Cold start: curves are seeded to the identity map,
so at launch the system behaves exactly like a naive ensemble, and a
`calibration_confidence` indicator is surfaced so early numbers are never
presented with false precision.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# Below this many resolved outcomes, isotonic fits are unreliable noise (07 §6).
CONFIDENCE_THRESHOLDS = {"low": 0, "medium": 100, "high": 250}

P_FLOOR, P_CEIL = 0.02, 0.98  # keep the log-opinion-pool away from 0/1 degeneracy


def clamp_p(p: float) -> float:
    return float(min(max(p, P_FLOOR), P_CEIL))


@dataclass
class CalibrationCurve:
    """Piecewise-linear isotonic curve stored as breakpoints
    ([{raw, calibrated}, ...] — 03_DATABASE §3.13)."""

    agent: str
    regime: str = "all"
    raw: list[float] = field(default_factory=lambda: [0.0, 1.0])
    calibrated: list[float] = field(default_factory=lambda: [0.0, 1.0])
    n_samples: int = 0
    version: str = "identity-seed"

    @classmethod
    def identity(cls, agent: str, regime: str = "all") -> "CalibrationCurve":
        return cls(agent=agent, regime=regime)

    def apply(self, p_raw: float) -> float:
        p = float(np.interp(p_raw, self.raw, self.calibrated))
        return clamp_p(p)

    @property
    def confidence(self) -> str:
        if self.n_samples >= CONFIDENCE_THRESHOLDS["high"]:
            return "high"
        if self.n_samples >= CONFIDENCE_THRESHOLDS["medium"]:
            return "medium"
        return "low"

    def to_json(self) -> dict:
        return {
            "agent": self.agent,
            "regime": self.regime,
            "curve": [
                {"raw": r, "calibrated": c} for r, c in zip(self.raw, self.calibrated)
            ],
            "n_samples": self.n_samples,
            "version": self.version,
        }

    @classmethod
    def from_json(cls, data: dict) -> "CalibrationCurve":
        pts = data.get("curve") or [{"raw": 0.0, "calibrated": 0.0}, {"raw": 1.0, "calibrated": 1.0}]
        return cls(
            agent=data["agent"],
            regime=data.get("regime", "all"),
            raw=[p["raw"] for p in pts],
            calibrated=[p["calibrated"] for p in pts],
            n_samples=data.get("n_samples", 0),
            version=data.get("version", "identity-seed"),
        )


def fit_isotonic(
    agent: str,
    p_raw: np.ndarray,
    outcomes: np.ndarray,
    regime: str = "all",
    version: str = "v1",
    min_samples: int = 30,
) -> CalibrationCurve:
    """Refit an agent's curve from the outcome log (weekly cadence, 07 §4).

    Below `min_samples` resolved outcomes, returns the identity seed — fitting
    isotonic regression on a handful of points is worse than not calibrating.
    """
    n = int(len(p_raw))
    if n < min_samples:
        curve = CalibrationCurve.identity(agent, regime)
        curve.n_samples = n
        return curve

    from sklearn.isotonic import IsotonicRegression

    iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
    iso.fit(p_raw, outcomes)
    xs = np.unique(np.concatenate([[0.0], np.asarray(iso.X_thresholds_), [1.0]]))
    ys = iso.predict(xs)
    return CalibrationCurve(
        agent=agent,
        regime=regime,
        raw=[float(x) for x in xs],
        calibrated=[float(y) for y in ys],
        n_samples=n,
        version=version,
    )
