from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quorum.models import FactPack


@pytest.fixture
def synthetic_ohlcv() -> pd.DataFrame:
    """~300 trading days of a gently up-trending, mildly noisy series."""
    rng = np.random.default_rng(42)
    n = 300
    drift = 0.0006
    rets = rng.normal(drift, 0.015, n)
    close = 900.0 * np.exp(np.cumsum(rets))
    high = close * (1 + np.abs(rng.normal(0, 0.006, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.006, n)))
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    volume = rng.integers(1_000_000, 5_000_000, n).astype(float)
    idx = pd.bdate_range(end="2026-07-08", periods=n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


@pytest.fixture
def golden_fact_pack(synthetic_ohlcv) -> tuple[FactPack, dict]:
    """Fixed fact pack for golden-debate regression tests (05 §9) — no network."""
    close = synthetic_ohlcv["close"]
    rng = np.random.default_rng(7)
    idx = synthetic_ohlcv.index
    macro_factors = {
        "market": pd.Series(rng.normal(0.0004, 0.01, len(idx)), index=idx),
        "usdinr": pd.Series(rng.normal(0.0001, 0.004, len(idx)), index=idx),
    }
    pack = FactPack(
        symbol="GOLDEN",
        exchange="NSE",
        price={
            "last": round(float(close.iloc[-1]), 2),
            "high_52w": round(float(close.tail(252).max()), 2),
            "low_52w": round(float(close.tail(252).min()), 2),
            "rows": len(synthetic_ohlcv),
        },
        fundamentals={
            "pe": 18.0, "pb": 2.4, "roe": 0.17, "debt_to_equity": 0.5,
            "revenue_cagr": 0.14, "eps_growth": 0.12, "mom_12_1": 0.20,
            "sector": "Auto",
        },
        macro={"factors_available": list(macro_factors.keys())},
        sources={"price": "ok", "fundamentals": "ok", "macro": "ok",
                 "flows": "missing", "catalysts": "missing"},
    )
    raw = {
        "ohlcv": synthetic_ohlcv,
        "stock_returns": close.pct_change().dropna(),
        "macro_factors": macro_factors,
        "summary_text": "",
    }
    return pack, raw
