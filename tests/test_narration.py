"""Narration validation (05 §6): the number-match check that catches an LLM
altering computed numbers, plus templated fallbacks."""

from __future__ import annotations

from quorum.agents.narration import narration_consistent
from quorum.agents.prompts import template_narration, template_rationale

VERDICT = {
    "action": "BUY",
    "entry": 912.0, "target": 978.0, "stop": 895.0,
    "risk_reward": 3.88,
    "p_bull_calibrated": 0.71,
    "expected_value": 26.4, "edge": 1.55, "hurdle_tau": 0.15,
    "kelly_fraction": 0.11, "position_size_pct": 0.04,
    "agent_weights": {"technician": 0.24, "fundamentalist": 0.31},
}


class TestNarrationConsistency:
    def test_verdict_numbers_pass(self):
        text = ("BUY at 912 with target 978 and stop 895. Calibrated probability "
                "0.71 gives edge 1.55 over the 0.15 hurdle.")
        assert narration_consistent(text, VERDICT)

    def test_invented_price_fails(self):
        text = "BUY at 912, but I think 1050 is the real target."
        assert not narration_consistent(text, VERDICT)

    def test_percent_rendering_passes(self):
        text = "The council puts a 71% probability on the bull case."
        assert narration_consistent(text, VERDICT)

    def test_small_counts_allowed(self):
        text = "All 5 agents weighed in over 30 days of data at 912."
        assert narration_consistent(text, VERDICT)

    def test_no_numbers_passes(self):
        assert narration_consistent("The council leans bullish here.", VERDICT)


class TestTemplates:
    def test_agent_template_carries_the_computed_number(self):
        t = template_narration("technician", 0.68, {"regime": "trend", "z_signed": 1.2})
        assert "0.68" in t and "BULL" in t

    def test_rationale_buy(self):
        r = template_rationale(VERDICT)
        assert "BUY" in r and "912" in str(r)

    def test_rationale_no_call_is_honest(self):
        v = dict(VERDICT, action="NO_CALL", edge=0.02)
        r = template_rationale(v)
        assert "NO_CALL" in r
        assert "does not clear" in r
