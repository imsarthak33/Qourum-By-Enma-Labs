"""Regression for a live crash chain (2026-07-10): yfinance handed back a
DataFrame whose FINAL bar had Close = NaN (an unsettled/incomplete session,
observed on RELIANCE.NS the day after data cutoff). `fetch_ohlcv` still
labelled the source "ok" and every downstream `.iloc[-1]` (price.last,
return_1m, the Risk Ranger's entry/ATR) silently absorbed the NaN, which
forced the Chairman onto the degraded edge=0.0 path and then blew up
narration's number-match check. Fixed at the one shared source: drop
NaN-close rows right after fetch, so no consumer needs to defend itself.
"""

from __future__ import annotations

import math
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from quorum.data.adapters import build_fact_pack, fetch_ohlcv


def _ohlcv_with_trailing_nan_bar(n: int = 300) -> pd.DataFrame:
    rng = np.random.default_rng(3)
    close = 1300.0 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, n)))
    idx = pd.bdate_range(end="2026-07-09", periods=n)
    df = pd.DataFrame({
        "Open": close, "High": close * 1.01, "Low": close * 0.99,
        "Close": close, "Volume": rng.integers(1_000_000, 5_000_000, n).astype(float),
    }, index=idx)
    df.loc[df.index[-1], ["Open", "High", "Low", "Close"]] = float("nan")
    return df


@pytest.fixture
def mocked_yf_with_bad_tail():
    fake_history = _ohlcv_with_trailing_nan_bar()
    fake_ticker = MagicMock()
    fake_ticker.history.return_value = fake_history
    fake_ticker.info = {
        "regularMarketPrice": 1275.9, "trailingPE": 24.1, "priceToBook": 2.1,
        "returnOnEquity": 0.09, "debtToEquity": 40.0, "revenueGrowth": 0.06,
        "earningsGrowth": 0.05, "sector": "Energy", "marketCap": 1.7e13,
        "longBusinessSummary": "",
    }
    fake_module = MagicMock()
    fake_module.Ticker.return_value = fake_ticker
    with patch("quorum.data.adapters._yf", return_value=fake_module):
        yield fake_history


class TestTrailingNaNBar:
    def test_fetch_ohlcv_drops_nan_close_rows(self, mocked_yf_with_bad_tail):
        df = fetch_ohlcv("RELIANCE", "NSE")
        assert df is not None
        assert not df["close"].isna().any()
        assert len(df) == len(mocked_yf_with_bad_tail) - 1  # only the bad bar dropped

    def test_fact_pack_price_last_is_finite_not_nan(self, mocked_yf_with_bad_tail):
        pack, raw = build_fact_pack("RELIANCE", "NSE")
        assert pack.sources["price"] == "ok"
        assert math.isfinite(pack.price["last"])
        assert math.isfinite(pack.price["return_1m"])
        # the real (pre-NaN) last close, not the poisoned trailing bar
        assert pack.price["last"] == round(float(mocked_yf_with_bad_tail["Close"].iloc[-2]), 2)

    def test_all_nan_frame_degrades_to_missing_not_a_fake_ok(self):
        all_nan = _ohlcv_with_trailing_nan_bar()
        all_nan[:] = float("nan")
        fake_ticker = MagicMock()
        fake_ticker.history.return_value = all_nan
        fake_module = MagicMock()
        fake_module.Ticker.return_value = fake_ticker
        with patch("quorum.data.adapters._yf", return_value=fake_module):
            assert fetch_ohlcv("RELIANCE", "NSE") is None
