"""The Chairman is a pure function — same inputs, same outputs, fully
unit-testable (07_QUANT_CORE §1). These tests pin that contract down."""

from __future__ import annotations

import math

import pytest

from quorum.models import Action, AgentFeature
from quorum.quant.calibration import CalibrationCurve
from quorum.quant.chairman import decide_action, log_opinion_pool, synthesize, validate_levels
from quorum.quant.weights import uniform_weights


def _feat(agent: str, p: float | None, extra: dict | None = None, triggered=True) -> AgentFeature:
    return AgentFeature(agent=agent, p_bull_raw=p, features=extra or {},
                        ok=p is not None, triggered=triggered)


def _risk_feat(entry=100.0, stop=95.0, target=110.0, p=0.5) -> AgentFeature:
    return _feat("risk", p, {
        "entry": entry, "stop": stop, "target": target, "daily_vol": 0.015,
    })


def _curves() -> dict[str, CalibrationCurve]:
    return {a: CalibrationCurve.identity(a)
            for a in ("technician", "fundamentalist", "macro", "devils_advocate", "risk")}


class TestLogOpinionPool:
    def test_unanimous_agreement(self):
        p = log_opinion_pool({"a": 0.7, "b": 0.7}, {"a": 0.5, "b": 0.5})
        assert p == pytest.approx(0.7, abs=1e-9)

    def test_symmetric_disagreement_is_neutral(self):
        p = log_opinion_pool({"a": 0.7, "b": 0.3}, {"a": 0.5, "b": 0.5})
        assert p == pytest.approx(0.5, abs=1e-9)

    def test_weight_tilts_the_pool(self):
        p = log_opinion_pool({"a": 0.8, "b": 0.3}, {"a": 0.8, "b": 0.2})
        assert p > 0.6

    def test_confident_and_wrong_penalised_multiplicatively(self):
        # An extreme opinion moves the log pool more than a linear average would.
        pool = log_opinion_pool({"a": 0.98, "b": 0.5}, {"a": 0.5, "b": 0.5})
        linear = (0.98 + 0.5) / 2
        assert pool > linear

    def test_empty_is_neutral(self):
        assert log_opinion_pool({}, {}) == 0.5

    def test_determinism(self):
        args = ({"a": 0.61, "b": 0.44, "c": 0.58}, {"a": 0.4, "b": 0.35, "c": 0.25})
        assert log_opinion_pool(*args) == log_opinion_pool(*args)


class TestDecideAction:
    def test_above_hurdle_buys(self):
        assert decide_action(0.3, 0.15, degraded=False) == Action.BUY

    def test_between_half_and_full_hurdle_waits(self):
        assert decide_action(0.1, 0.15, degraded=False) == Action.WAIT

    def test_below_half_hurdle_no_call(self):
        assert decide_action(0.05, 0.15, degraded=False) == Action.NO_CALL
        assert decide_action(-0.4, 0.15, degraded=False) == Action.NO_CALL

    def test_degraded_above_hurdle_avoids(self):
        assert decide_action(0.3, 0.15, degraded=True) == Action.AVOID


class TestSynthesize:
    def test_full_council_bullish(self):
        features = {
            "technician": _feat("technician", 0.72),
            "fundamentalist": _feat("fundamentalist", 0.68),
            "macro": _feat("macro", 0.61),
            "devils_advocate": _feat("devils_advocate", None, triggered=False),
            "risk": _risk_feat(p=0.55),
        }
        verdict, p_hats, degraded = synthesize(features, _curves(), uniform_weights())
        assert not degraded
        assert verdict.p_bull_calibrated > 0.6
        assert verdict.action in (Action.BUY, Action.WAIT)
        assert verdict.entry == 100.0
        # weights renormalise over the 4 responding agents and sum to 1
        assert sum(verdict.agent_weights.values()) == pytest.approx(1.0, abs=1e-3)
        assert "devils_advocate" not in verdict.agent_weights

    def test_degraded_when_primary_fails(self):
        features = {
            "technician": _feat("technician", None),
            "fundamentalist": _feat("fundamentalist", 0.6),
            "macro": _feat("macro", 0.55),
            "devils_advocate": _feat("devils_advocate", None, triggered=False),
            "risk": _risk_feat(p=0.5),
        }
        verdict, _, degraded = synthesize(features, _curves(), uniform_weights())
        assert degraded
        # weights are rounded to 4dp for the audit record — allow that quantum
        assert sum(verdict.agent_weights.values()) == pytest.approx(1.0, abs=1e-3)

    def test_bearish_pool_mirrors_levels_for_sell(self):
        features = {
            "technician": _feat("technician", 0.15),
            "fundamentalist": _feat("fundamentalist", 0.2),
            "macro": _feat("macro", 0.25),
            "devils_advocate": _feat("devils_advocate", None, triggered=False),
            "risk": _risk_feat(entry=100.0, stop=95.0, target=110.0, p=0.4),
        }
        verdict, _, _ = synthesize(features, _curves(), uniform_weights())
        if verdict.action == Action.SELL:
            assert verdict.target < verdict.entry < verdict.stop

    def test_no_levels_means_degraded_no_call(self):
        features = {
            "technician": _feat("technician", 0.7),
            "fundamentalist": _feat("fundamentalist", 0.7),
            "macro": _feat("macro", 0.7),
            "risk": _feat("risk", None),
        }
        verdict, _, degraded = synthesize(features, _curves(), uniform_weights())
        assert degraded
        assert verdict.action in (Action.NO_CALL, Action.WAIT, Action.AVOID)

    def test_kelly_only_on_actionable_calls(self):
        features = {
            "technician": _feat("technician", 0.5),
            "fundamentalist": _feat("fundamentalist", 0.5),
            "macro": _feat("macro", 0.5),
            "devils_advocate": _feat("devils_advocate", None, triggered=False),
            "risk": _risk_feat(p=0.5),
        }
        verdict, _, _ = synthesize(features, _curves(), uniform_weights())
        assert verdict.action in (Action.WAIT, Action.NO_CALL)
        assert verdict.position_size_pct is None

    def test_neutral_council_never_clears_hurdle(self):
        """Regression: edge must measure information, not payoff asymmetry.
        A P=0.5 council with a 2R target must score ~zero edge, not 0.5."""
        features = {
            "technician": _feat("technician", 0.5),
            "fundamentalist": _feat("fundamentalist", 0.5),
            "macro": _feat("macro", 0.5),
            "devils_advocate": _feat("devils_advocate", None, triggered=False),
            "risk": _risk_feat(entry=100.0, stop=95.0, target=110.0, p=0.5),
        }
        verdict, _, _ = synthesize(features, _curves(), uniform_weights())
        assert verdict.edge == pytest.approx(0.0, abs=1e-6)
        assert verdict.action == Action.NO_CALL

    def test_calibration_curve_actually_applied(self):
        # A curve that compresses everything toward 0.5 must lower the pool.
        curves = _curves()
        curves["technician"] = CalibrationCurve(
            agent="technician", raw=[0.0, 1.0], calibrated=[0.4, 0.6]
        )
        features = {
            "technician": _feat("technician", 0.95),
            "fundamentalist": _feat("fundamentalist", 0.5),
            "macro": _feat("macro", 0.5),
            "devils_advocate": _feat("devils_advocate", None, triggered=False),
            "risk": _risk_feat(p=0.5),
        }
        verdict_flat, p_hats, _ = synthesize(features, curves, uniform_weights())
        assert p_hats["technician"] == pytest.approx(0.59, abs=0.01)

    def test_determinism_of_full_pipeline(self):
        features = {
            "technician": _feat("technician", 0.66),
            "fundamentalist": _feat("fundamentalist", 0.31),
            "macro": _feat("macro", 0.52),
            "devils_advocate": _feat("devils_advocate", 0.35),
            "risk": _risk_feat(p=0.48),
        }
        v1, _, _ = synthesize(features, _curves(), uniform_weights())
        v2, _, _ = synthesize(features, _curves(), uniform_weights())
        assert v1.to_json() == v2.to_json()


class TestValidateLevels:
    def test_levels_in_band_pass(self):
        from quorum.models import Verdict

        v = Verdict(action=Action.BUY, entry=100, target=110, stop=95,
                    risk_reward=2.0, p_bull_calibrated=0.6, expected_value=4.0,
                    edge=0.8, hurdle_tau=0.15, kelly_fraction=0.1,
                    position_size_pct=0.03, agent_weights={},
                    calibration_confidence="low")
        assert validate_levels(v, {"low_52w": 80, "high_52w": 120})
        assert not validate_levels(v, {"low_52w": 300, "high_52w": 400})
        assert validate_levels(v, {})  # nothing to validate against


class TestNaNResilience:
    """Regression for the live crash (2026-07-10): stale yfinance frames handed
    the Risk Ranger NaN levels; NaN passes `is not None`, reached
    verdict.to_json(), and blew up narration's number-check with
    `ValueError: cannot convert float NaN to integer`. NaN must mean MISSING."""

    def test_nan_levels_become_none_and_json_stays_valid(self):
        import json
        features = {
            "technician": _feat("technician", 0.5),
            "fundamentalist": _feat("fundamentalist", 0.44),
            "macro": _feat("macro", 0.36),
            "risk": _risk_feat(entry=float("nan"), stop=float("nan"), target=float("nan")),
        }
        verdict, _, degraded = synthesize(features, _curves(), uniform_weights())
        assert verdict.entry is None and verdict.stop is None and verdict.target is None
        assert degraded is True  # missing levels = degraded path, honestly flagged
        # allow_nan=False raises if any NaN survived anywhere in the payload —
        # this is exactly what the SSE bridge / extension JSON.parse needs.
        json.dumps(verdict.to_json(), allow_nan=False)

    def test_nan_probability_excluded_like_a_failed_model(self):
        features = {
            "technician": _feat("technician", float("nan")),
            "fundamentalist": _feat("fundamentalist", 0.6),
            "macro": _feat("macro", 0.6),
            "risk": _risk_feat(p=0.6),
        }
        verdict, p_hats, degraded = synthesize(features, _curves(), uniform_weights())
        assert "technician" not in p_hats           # excluded, not aggregated
        assert degraded is True                      # a failed primary = degraded
        assert math.isfinite(verdict.p_bull_calibrated)

    def test_narration_number_check_tolerates_nan_verdict_fields(self):
        from quorum.agents.narration import narration_consistent
        vjson = {"action": "WAIT", "entry": float("nan"), "edge": 0.081,
                 "agent_weights": {"technician": float("nan")}}
        # Must not raise; the check simply ignores non-finite fields.
        assert narration_consistent("edge of 0.081 on this setup", vjson) is True
