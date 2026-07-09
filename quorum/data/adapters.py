"""Data adapters (02_TRD §6): fetch(symbol, exchange) -> dict, degrading
gracefully — partial data comes back with `stale`/`missing` flags in
`sources`, never as an exception. Free sources only (00 §5 cost model).

yfinance covers OHLCV, basic fundamentals, and macro proxies (NIFTY, USDINR,
Brent). FII/DII flows and options PCR have no reliable free API — those keys
are returned `missing` and the models that want them run on a reduced set.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from ..models import FactPack

YF_SUFFIX = {"NSE": ".NS", "BSE": ".BO"}

MACRO_TICKERS = {
    "market": "^NSEI",       # NIFTY 50
    "usdinr": "USDINR=X",
    "oil": "BZ=F",           # Brent
}


def _yf():
    import yfinance  # imported lazily so unit tests never need network

    return yfinance


def fetch_ohlcv(symbol: str, exchange: str = "NSE", period: str = "2y") -> pd.DataFrame | None:
    try:
        t = _yf().Ticker(f"{symbol}{YF_SUFFIX.get(exchange, '.NS')}")
        df = t.history(period=period, interval="1d", auto_adjust=True)
        if df is None or df.empty:
            return None
        df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
        df.index = pd.to_datetime(df.index).tz_localize(None)
        return df
    except Exception:  # noqa: BLE001 — degrade, don't raise (02_TRD §6)
        return None


def fetch_fundamentals(symbol: str, exchange: str = "NSE") -> dict[str, Any] | None:
    try:
        t = _yf().Ticker(f"{symbol}{YF_SUFFIX.get(exchange, '.NS')}")
        info = t.info or {}
        if not info.get("regularMarketPrice") and not info.get("trailingPE"):
            return None
        d2e = info.get("debtToEquity")
        out = {
            "pe": info.get("trailingPE"),
            "pb": info.get("priceToBook"),
            "roe": info.get("returnOnEquity"),
            "debt_to_equity": (d2e / 100.0) if isinstance(d2e, (int, float)) else None,
            "revenue_cagr": info.get("revenueGrowth"),
            "eps_growth": info.get("earningsGrowth"),
            "sector": info.get("sector"),
            "market_cap": info.get("marketCap"),
            "summary": (info.get("longBusinessSummary") or "")[:1500],
        }
        return out
    except Exception:  # noqa: BLE001
        return None


def fetch_macro_factors(period: str = "1y") -> dict[str, pd.Series]:
    """Daily pct-change series per macro factor; missing factors are dropped."""
    out: dict[str, pd.Series] = {}
    for name, ticker in MACRO_TICKERS.items():
        try:
            df = _yf().Ticker(ticker).history(period=period, interval="1d")
            if df is None or df.empty:
                continue
            s = df["Close"].pct_change().dropna()
            s.index = pd.to_datetime(s.index).tz_localize(None)
            out[name] = s
        except Exception:  # noqa: BLE001
            continue
    return out


def momentum_12_1(ohlcv: pd.DataFrame) -> float | None:
    """12-1 month return: last month excluded (short-term reversal control,
    07 §2.2)."""
    if ohlcv is None or len(ohlcv) < 260:
        return None
    close = ohlcv["close"]
    p_12m, p_1m = float(close.iloc[-252]), float(close.iloc[-21])
    if p_12m <= 0:
        return None
    return p_1m / p_12m - 1.0


def build_fact_pack(symbol: str, exchange: str = "NSE") -> tuple[FactPack, dict[str, Any]]:
    """Assemble the fact pack + raw frames the quant models need.

    Returns (fact_pack, raw) where raw = {"ohlcv": DataFrame|None,
    "stock_returns": Series|None, "macro_factors": {name: Series}}.
    """
    sources: dict[str, str] = {}

    ohlcv = fetch_ohlcv(symbol, exchange)
    sources["price"] = "ok" if ohlcv is not None else "missing"

    fundamentals = fetch_fundamentals(symbol, exchange)
    sources["fundamentals"] = "ok" if fundamentals else "missing"

    macro_factors = fetch_macro_factors()
    sources["macro"] = "ok" if macro_factors else "missing"
    sources["flows"] = "missing"      # FII/DII: no free API — reduced factor set
    sources["catalysts"] = "missing"  # earnings calendar: not wired yet

    price: dict[str, Any] = {}
    stock_returns = None
    if ohlcv is not None:
        close = ohlcv["close"]
        price = {
            "last": round(float(close.iloc[-1]), 2),
            "high_52w": round(float(close.tail(252).max()), 2),
            "low_52w": round(float(close.tail(252).min()), 2),
            "return_1m": round(float(close.iloc[-1] / close.iloc[-21] - 1.0), 4)
            if len(close) > 21 else None,
            "rows": len(ohlcv),
        }
        stock_returns = close.pct_change().dropna()
        mom = momentum_12_1(ohlcv)
        if fundamentals is not None and mom is not None:
            fundamentals["mom_12_1"] = round(mom, 4)

    pack = FactPack(
        symbol=symbol,
        exchange=exchange,
        price=price,
        fundamentals={k: v for k, v in (fundamentals or {}).items() if k != "summary"},
        macro={"factors_available": list(macro_factors.keys())},
        flows={},
        catalysts={},
        sources=sources,
    )
    raw = {
        "ohlcv": ohlcv,
        "stock_returns": stock_returns,
        "macro_factors": macro_factors,
        "summary_text": (fundamentals or {}).get("summary", ""),
    }
    return pack, raw
