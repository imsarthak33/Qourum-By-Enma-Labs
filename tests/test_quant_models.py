"""Per-agent feature models (07 §2): each produces a probability from data
alone, degrades gracefully on missing inputs, and never needs a provider."""

from __future__ import annotations

import pandas as pd
import pytest

from quorum.quant import devils_advocate, fundamentalist, macro, risk, technician


class TestTechnician:
    def test_produces_probability(self, synthetic_ohlcv):
        feat = technician.compute(synthetic_ohlcv)
        assert feat.ok
        assert 0.0 < feat.p_bull_raw < 1.0
        assert feat.features["regime"] in ("trend", "reversion")
        assert "z_signed" in feat.features

    def test_insufficient_data_degrades(self):
        feat = technician.compute(None)
        assert not feat.ok and feat.p_bull_raw is None
        feat = technician.compute(pd.DataFrame({"open": [1], "high": [1],
                                                "low": [1], "close": [1], "volume": [1]}))
        assert not feat.ok

    def test_deterministic(self, synthetic_ohlcv):
        a = technician.compute(synthetic_ohlcv)
        b = technician.compute(synthetic_ohlcv)
        assert a.p_bull_raw == b.p_bull_raw


class TestFundamentalist:
    FUND = {"pe": 12.0, "pb": 1.5, "roe": 0.22, "debt_to_equity": 0.3,
            "revenue_cagr": 0.18, "eps_growth": 0.20, "mom_12_1": 0.25}

    def test_cheap_quality_growth_is_bullish(self):
        feat = fundamentalist.compute(self.FUND)
        assert feat.ok
        assert feat.p_bull_raw > 0.6

    def test_expensive_junk_is_bearish(self):
        feat = fundamentalist.compute({"pe": 80.0, "pb": 12.0, "roe": 0.02,
                                       "debt_to_equity": 2.5, "revenue_cagr": -0.05,
                                       "eps_growth": -0.10, "mom_12_1": -0.30})
        assert feat.p_bull_raw < 0.4

    def test_reduced_factor_set_flagged(self):
        feat = fundamentalist.compute({"pe": 20.0})
        assert feat.ok
        assert feat.features["reduced_factor_set"] is True

    def test_no_data_fails_gracefully(self):
        feat = fundamentalist.compute(None)
        assert not feat.ok and feat.p_bull_raw is None

    def test_delta_qual_moves_the_needle(self):
        base = fundamentalist.compute(self.FUND, delta_qual=0.0)
        pos = fundamentalist.compute(self.FUND, delta_qual=1.0)
        assert pos.p_bull_raw > base.p_bull_raw


class TestMacro:
    def test_produces_probability(self, golden_fact_pack):
        _, raw = golden_fact_pack
        feat = macro.compute(raw["stock_returns"], raw["macro_factors"])
        assert feat.ok
        assert 0.0 < feat.p_bull_raw < 1.0
        assert set(feat.features["betas"]) == {"market", "usdinr"}

    def test_missing_factors_degrade(self):
        feat = macro.compute(None, None)
        assert not feat.ok

    def test_sector_pc1_orientation(self, synthetic_ohlcv):
        import numpy as np

        rng = np.random.default_rng(3)
        idx = synthetic_ohlcv.index
        common = rng.normal(0, 0.01, len(idx))
        sectors = pd.DataFrame(
            {f"s{i}": common + rng.normal(0, 0.003, len(idx)) for i in range(5)},
            index=idx,
        )
        pc1 = macro.sector_pc1(sectors)
        assert pc1 is not None
        # PC1 must positively track the average sector move
        assert np.corrcoef(pc1, sectors.mean(axis=1))[0, 1] > 0.9


class TestDevilsAdvocate:
    def test_fires_on_large_disagreement(self):
        feat = devils_advocate.compute(p_tech=0.85, p_fund=0.25, p_macro=0.6)
        assert feat.triggered
        assert feat.p_bull_raw is not None
        assert feat.features["triggered_by"] == "disagreement"
        # consensus is bullish (mean ≈ 0.57) so the contrarian leans bear
        assert feat.p_bull_raw < 0.5

    def test_silent_when_agents_agree(self):
        feat = devils_advocate.compute(p_tech=0.6, p_fund=0.58, p_macro=0.62)
        assert not feat.triggered
        assert feat.p_bull_raw is None
        assert feat.ok  # not-firing is the expected common case, not a failure

    def test_fires_on_crowding(self):
        feat = devils_advocate.compute(p_tech=0.55, p_fund=0.5, p_macro=0.5,
                                       crowding={"z_pcr": 2.7, "z_fii_delta": None})
        assert feat.triggered
        assert feat.features["triggered_by"] == "crowding"

    def test_needs_both_primaries(self):
        feat = devils_advocate.compute(p_tech=None, p_fund=0.6, p_macro=0.5)
        assert not feat.ok and not feat.triggered


class TestRisk:
    def test_levels_and_probability(self, synthetic_ohlcv):
        feat = risk.compute(synthetic_ohlcv, atr_stop_k=1.8)
        assert feat.ok
        f = feat.features
        assert f["stop"] < f["entry"] < f["target"]
        assert f["payoff_b"] == pytest.approx(2.0, abs=0.01)
        assert 0.35 <= feat.p_bull_raw <= 0.65

    def test_kelly_formula(self):
        sizing = risk.kelly_size(p_bull=0.6, entry=100, target=110, stop=95,
                                 kelly_lambda=1.0, daily_vol=0.001)
        # b=2, f* = (0.6*2 - 0.4)/2 = 0.4 — tiny vol so the CVaR cap won't bind
        assert sizing["kelly_fraction"] == pytest.approx(0.4)

    def test_negative_edge_kelly_is_zero(self):
        sizing = risk.kelly_size(p_bull=0.3, entry=100, target=110, stop=95)
        assert sizing["kelly_fraction"] == 0.0

    def test_cvar_cap_binds_when_vol_high(self):
        capped = risk.kelly_size(p_bull=0.9, entry=100, target=110, stop=95,
                                 kelly_lambda=1.0, daily_vol=0.05)
        uncapped = risk.kelly_size(p_bull=0.9, entry=100, target=110, stop=95,
                                   kelly_lambda=1.0, daily_vol=0.001)
        assert capped["position_size_pct"] < uncapped["position_size_pct"]

    def test_bad_levels_return_none(self):
        sizing = risk.kelly_size(p_bull=0.6, entry=100, target=90, stop=95)
        assert sizing["kelly_fraction"] is None
