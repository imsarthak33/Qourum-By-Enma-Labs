"""Onboarding roast (growth plan Horizon 1): a fast, quant-only read across a
watchlist or a handful of positions, plus a personalized "trading DNA" pattern
across the whole set. The viral first-touch - no blank "what do I even ask"
box, no 12-second council wait.

Deliberately NOT a tracked call: no LLM narration, no persistence, no
leaderboard. The slow part of a full debate is the per-agent LLM narration;
the quant feature models are milliseconds, so a roast that runs the same Quant
Core WITHOUT narration is genuinely immediate. Fact packs for the whole set
are fetched concurrently.

Boundary (doc 08 par.4, extended to the roast): every number here comes from
the same deterministic models `run_debate` uses - `read_one` calls the real
feature computes and the real Chairman. The sharp framing is deterministic
template text keyed off those computed numbers. Enma narrates; math decides.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from .config import QuorumConfig
from .quant import chairman, devils_advocate, fundamentalist, macro, risk, technician
from .storage import Storage

# Above this many ATRs over the 20-day line, price is "stretched" - buying
# strength, not value (technician's own z_tech, in ATR units).
STRETCH_ATR = 1.0
# Mean annualised vol above this reads as a deliberately spicy book.
SPICY_VOL = 0.45


@dataclass
class SymbolRead:
    symbol: str
    exchange: str
    side: int  # +1 you're long, -1 short, 0 just watching
    ok: bool
    error: str | None = None
    action: str | None = None
    p_bull: float | None = None
    edge: float | None = None
    # Salient per-symbol features pulled out for the DNA aggregation.
    regime: str | None = None
    z_tech: float | None = None
    ann_vol: float | None = None
    z_value: float | None = None
    sector: str | None = None

    def to_json(self) -> dict[str, Any]:
        """The subset the Enma overlay renders. Numbers only - the overlay,
        like the CLI, shows these verbatim and never recomputes."""
        return {
            "symbol": self.symbol, "exchange": self.exchange, "side": self.side,
            "ok": self.ok, "error": self.error, "action": self.action,
            "p_bull": self.p_bull, "edge": self.edge,
        }


@dataclass
class RoastResult:
    reads: list[SymbolRead]
    archetype: str
    tells: list[str] = field(default_factory=list)

    @property
    def ok_reads(self) -> list[SymbolRead]:
        return [r for r in self.reads if r.ok]

    def to_json(self) -> dict[str, Any]:
        return {
            "archetype": self.archetype,
            "tells": list(self.tells),
            "reads": [r.to_json() for r in self.reads],
        }


def parse_entry(raw: str) -> tuple[str, str, int] | None:
    """`[+|-][EXCHANGE:]SYMBOL` -> (exchange, symbol, side). A leading + means
    you're long it, - means short, neither means you're just watching. Bare
    symbols default to NSE. Returns None for junk."""
    s = raw.strip()
    if not s:
        return None
    side = 0
    if s[0] in "+-":
        side = 1 if s[0] == "+" else -1
        s = s[1:].strip()
    exchange, _, symbol = s.rpartition(":")
    if not exchange:
        exchange = "NSE"
    symbol = symbol.strip().upper()
    if not symbol:
        return None
    return exchange.strip().upper(), symbol, side


def read_one(exchange: str, symbol: str, side: int,
             curves: dict[str, Any], weights: dict[str, float],
             config: QuorumConfig) -> SymbolRead:
    """One quant-only read: fact pack -> feature models -> Chairman. No
    narration, no persistence. Mirrors the deterministic half of `run_debate`
    (steps 1-3, 6-7) exactly so the numbers match a full debate's."""
    from .data import build_fact_pack

    try:
        fact_pack, raw = build_fact_pack(symbol, exchange)
    except Exception as exc:  # noqa: BLE001 - a bad symbol/feed is a per-row miss
        return SymbolRead(symbol, exchange, side, ok=False, error=str(exc))
    if fact_pack.sources.get("price") == "missing":
        return SymbolRead(symbol, exchange, side, ok=False, error="no price data")

    ohlcv = raw.get("ohlcv")
    features = {
        "technician": technician.compute(ohlcv),
        "fundamentalist": fundamentalist.compute(fact_pack.fundamentals or None),
        "macro": macro.compute(raw.get("stock_returns"), raw.get("macro_factors")),
        "risk": risk.compute(ohlcv, config.atr_stop_k),
    }
    features["devils_advocate"] = devils_advocate.compute(
        p_tech=features["technician"].p_bull_raw,
        p_fund=features["fundamentalist"].p_bull_raw,
        p_macro=features["macro"].p_bull_raw,
        crowding=None,
    )
    verdict, _p_hats, _degraded = chairman.synthesize(
        features, curves, weights,
        hurdle_tau=config.hurdle_tau, kelly_lambda=config.kelly_lambda,
    )
    v = verdict.to_json()
    tech_f = features["technician"].features or {}
    risk_f = features["risk"].features or {}
    fund_f = features["fundamentalist"].features or {}
    return SymbolRead(
        symbol=symbol, exchange=exchange, side=side, ok=True,
        action=v["action"], p_bull=v["p_bull_calibrated"], edge=v["edge"],
        regime=tech_f.get("regime"), z_tech=tech_f.get("z_tech"),
        ann_vol=risk_f.get("annualised_vol"), z_value=fund_f.get("z_value"),
        sector=fund_f.get("sector"),
    )


def roast(entries: list[tuple[str, str, int]], config: QuorumConfig) -> RoastResult:
    """Read every entry (concurrently) and derive the trading-DNA tells."""
    storage = Storage(config.db_path)
    curves = storage.latest_curves()
    weights = storage.latest_weights()

    # Fact packs are network-bound and independent; fetch them in parallel so a
    # watchlist is "immediate" rather than N sequential round-trips. The
    # workers share only read-only curves/weights and touch no sqlite.
    max_workers = min(8, max(1, len(entries)))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        reads = list(pool.map(
            lambda e: read_one(e[0], e[1], e[2], curves, weights, config), entries
        ))
    tells, archetype = _trading_dna(reads)
    return RoastResult(reads=reads, archetype=archetype, tells=tells)


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _trading_dna(reads: list[SymbolRead]) -> tuple[list[str], str]:
    """Derive the personalized pattern. Every tell is guarded by data
    availability and carries a real engine-computed number - no tell fires on
    thin air, and the numbers are never invented here."""
    ok = [r for r in reads if r.ok]
    n = len(ok)
    if n == 0:
        return [], "No read - none of those resolved against live data."

    actionable = [r for r in ok if r.action in ("BUY", "SELL")]
    stretched = [r for r in ok if r.z_tech is not None and r.z_tech > STRETCH_ATR]
    vols = [r.ann_vol for r in ok if r.ann_vol is not None]
    zvals = [r.z_value for r in ok if r.z_value is not None]
    sectors = Counter(r.sector for r in ok if r.sector)
    top_sector, top_sector_n = (sectors.most_common(1)[0] if sectors else (None, 0))
    mean_vol = _mean(vols)
    mean_zval = _mean(zvals)

    tells: list[str] = []

    # Council disagreement with positions you actually hold - the sharpest tell.
    against = [r for r in ok if (r.side == 1 and r.action == "SELL")
               or (r.side == -1 and r.action == "BUY")]
    if against:
        names = ", ".join(f"{r.symbol}" for r in against)
        tells.append(f"You're positioned against the council on {len(against)} of your "
                     f"names ({names}) - it leans the other way on each.")

    # How much of the book actually clears the bar.
    if n >= 2:
        tells.append(f"The council clears its hurdle on {len(actionable)} of {n} - "
                     + ("the rest are coin flips you'd pay the spread to hold."
                        if len(actionable) < n else "a rare all-green book."))

    # Momentum chasing.
    if n >= 2 and len(stretched) / n >= 0.5:
        tells.append(f"{len(stretched)} of {n} are stretched over "
                     f"{STRETCH_ATR:g} ATR above their 20-day line - you're buying "
                     "strength, not value.")

    # Sector concentration.
    if top_sector and top_sector_n >= max(2, round(0.6 * n)):
        tells.append(f"{top_sector_n} of {n} are {top_sector} - that's not a "
                     "portfolio, it's one bet wearing five tickers.")

    # Volatility appetite.
    if mean_vol is not None and mean_vol > SPICY_VOL:
        tells.append(f"Average annualised vol across the book is {mean_vol * 100:.0f}% - "
                     "you like it loud.")

    # Valuation posture (z_value > 0 is cheap vs prior; < 0 is rich).
    if mean_zval is not None and mean_zval < -0.4:
        tells.append("Every name skews rich versus its own history - you're paying up "
                     "across the board.")
    elif mean_zval is not None and mean_zval > 0.5:
        tells.append("The book skews cheap versus history - contrarian value, whether "
                     "you meant it or not.")

    archetype = _archetype(n, actionable, stretched, mean_vol, mean_zval,
                           top_sector, top_sector_n)
    return tells, archetype


def _archetype(n, actionable, stretched, mean_vol, mean_zval,
               top_sector, top_sector_n) -> str:
    """One shareable label. First matching rule wins; ordered by how strongly
    each signal, when present, defines the trader."""
    if n >= 2 and not actionable:
        return "The Overtrader - the council won't back a single one at its hurdle."
    if n and len(stretched) / n >= 0.5 and (mean_zval is not None and mean_zval < -0.3):
        return "The Momentum Chaser - buying strength at rich prices."
    if top_sector and top_sector_n >= max(2, round(0.6 * n)):
        return f"All-In on {top_sector} - concentration is the whole strategy."
    if mean_vol is not None and mean_vol > SPICY_VOL:
        return "The Adrenaline Trader - a high-volatility book by choice."
    if mean_zval is not None and mean_zval > 0.5:
        return "The Contrarian - cheap names the tape currently hates."
    return "The Mixed Book - no single tell dominates yet."
