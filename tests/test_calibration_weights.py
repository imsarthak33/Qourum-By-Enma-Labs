"""Calibration monotonicity + Hedge-weight normalisation — the unit coverage
called out in 02_TRD §11."""

from __future__ import annotations

import numpy as np
import pytest

from quorum.quant.calibration import CalibrationCurve, clamp_p, fit_isotonic
from quorum.quant.weights import hedge_update, log_loss, renormalise, uniform_weights


class TestCalibration:
    def test_identity_seed_passthrough(self):
        c = CalibrationCurve.identity("technician")
        for p in (0.1, 0.33, 0.5, 0.77):
            assert c.apply(p) == pytest.approx(p)
        assert c.confidence == "low"
        assert c.version == "identity-seed"

    def test_clamping_away_from_degenerate_probs(self):
        c = CalibrationCurve.identity("x")
        assert c.apply(0.0) == clamp_p(0.0) > 0
        assert c.apply(1.0) == clamp_p(1.0) < 1

    def test_fit_returns_identity_below_min_samples(self):
        p = np.array([0.4, 0.6, 0.7])
        y = np.array([0.0, 1.0, 1.0])
        curve = fit_isotonic("technician", p, y, min_samples=30)
        assert curve.version == "identity-seed"
        assert curve.n_samples == 3

    def test_fitted_curve_is_monotonic(self):
        rng = np.random.default_rng(0)
        p = rng.uniform(0, 1, 500)
        # true outcome prob is an S-curve of p, plus noise
        y = (rng.uniform(0, 1, 500) < (0.2 + 0.6 * p)).astype(float)
        curve = fit_isotonic("technician", p, y, min_samples=30)
        assert curve.n_samples == 500
        applied = [curve.apply(x) for x in np.linspace(0, 1, 50)]
        assert all(b >= a - 1e-9 for a, b in zip(applied, applied[1:]))

    def test_confidence_thresholds(self):
        c = CalibrationCurve.identity("x")
        c.n_samples = 150
        assert c.confidence == "medium"
        c.n_samples = 300
        assert c.confidence == "high"

    def test_roundtrip_json(self):
        c = CalibrationCurve(agent="macro", raw=[0.0, 0.5, 1.0],
                             calibrated=[0.1, 0.45, 0.9], n_samples=42, version="v2")
        c2 = CalibrationCurve.from_json(c.to_json())
        assert c2.apply(0.25) == pytest.approx(c.apply(0.25))
        assert c2.n_samples == 42


class TestHedge:
    def test_uniform_seed_sums_to_one(self):
        w = uniform_weights()
        assert sum(w.values()) == pytest.approx(1.0)
        assert len(set(w.values())) == 1

    def test_update_punishes_higher_loss(self):
        w = uniform_weights(("a", "b"))
        losses = {"a": 2.0, "b": 0.1}
        w2 = hedge_update(w, losses, eta=0.5)
        assert w2["b"] > w2["a"]
        assert sum(w2.values()) == pytest.approx(1.0)

    def test_absent_agent_keeps_relative_weight(self):
        w = {"a": 0.5, "b": 0.3, "c": 0.2}
        w2 = hedge_update(w, {"a": 1.0}, eta=0.5)
        # b and c keep their ratio
        assert w2["b"] / w2["c"] == pytest.approx(0.3 / 0.2)
        assert w2["a"] < 0.5

    def test_log_loss_directionality(self):
        assert log_loss(0.9, True) < log_loss(0.6, True)
        assert log_loss(0.9, False) > log_loss(0.6, False)

    def test_renormalise_over_responders(self):
        w = uniform_weights()
        sub = renormalise(w, ["technician", "risk"])
        assert set(sub) == {"technician", "risk"}
        assert sum(sub.values()) == pytest.approx(1.0)

    def test_renormalise_empty_weights_fallback(self):
        sub = renormalise({"a": 0.0, "b": 0.0}, ["a", "b"])
        assert sum(sub.values()) == pytest.approx(1.0)
