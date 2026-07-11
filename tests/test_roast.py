"""Onboarding roast (growth plan Horizon 1). The quant models themselves are
covered by test_quant_models; here we cover the roast's own novel logic -
entry parsing, the trading-DNA tells + archetype, the fast/no-persist wiring,
and one real read_one against the golden fact pack (numbers must come from the
engine, nothing saved)."""

from __future__ import annotations

from unittest.mock import patch

from quorum.config import QuorumConfig
from quorum.roast import (
    RoastResult, SymbolRead, parse_entry, read_one, roast, _trading_dna,
)
from quorum.storage import Storage


def _r(symbol, action="WAIT", *, side=0, z_tech=None, z_value=None,
       ann_vol=None, sector=None, ok=True):
    return SymbolRead(symbol=symbol, exchange="NSE", side=side, ok=ok,
                      action=action, p_bull=0.5, edge=0.0, z_tech=z_tech,
                      z_value=z_value, ann_vol=ann_vol, sector=sector)


class TestParseEntry:
    def test_sides_exchange_and_bare(self):
        assert parse_entry("+RELIANCE") == ("NSE", "RELIANCE", 1)
        assert parse_entry("-NASDAQ:AAPL") == ("NASDAQ", "AAPL", -1)
        assert parse_entry("infy") == ("NSE", "INFY", 0)
        assert parse_entry("BSE:tcs") == ("BSE", "TCS", 0)

    def test_junk_is_dropped(self):
        assert parse_entry("   ") is None
        assert parse_entry("+") is None
        assert parse_entry("NSE:") is None


class TestTradingDNA:
    def test_momentum_chaser(self):
        reads = [_r(s, "BUY", z_tech=1.5, z_value=-0.6) for s in ("A", "B", "C", "D")]
        tells, archetype = _trading_dna(reads)
        assert archetype.startswith("The Momentum Chaser")
        assert any("stretched" in t for t in tells)
        assert any("rich" in t for t in tells)

    def test_overtrader_when_nothing_clears(self):
        reads = [_r(s, "WAIT") for s in ("A", "B", "C")]
        tells, archetype = _trading_dna(reads)
        assert archetype.startswith("The Overtrader")
        assert any("0 of 3" in t for t in tells)

    def test_sector_concentration(self):
        reads = ([_r(s, "BUY", sector="Auto") for s in ("A", "B", "C", "D")]
                 + [_r("E", "WAIT", sector="IT")])
        tells, archetype = _trading_dna(reads)
        assert "All-In on Auto" in archetype
        assert any("Auto" in t and "one bet" in t for t in tells)

    def test_positioned_against_the_council(self):
        reads = [_r("A", "SELL", side=1), _r("B", "BUY", side=-1), _r("C", "BUY", side=1)]
        tells, _ = _trading_dna(reads)
        against = [t for t in tells if "against the council" in t]
        assert against and "A" in against[0] and "B" in against[0] and "C" not in against[0]

    def test_no_reads_resolves_gracefully(self):
        tells, archetype = _trading_dna([_r("A", ok=False)])
        assert tells == []
        assert "No read" in archetype


class TestRoastWiring:
    def test_maps_entries_in_order_without_persisting(self, tmp_path):
        cfg = QuorumConfig()
        cfg.db_path = tmp_path / "roast.db"
        entries = [("NSE", "RELIANCE", 1), ("NASDAQ", "AAPL", 0)]

        def fake_read_one(exchange, symbol, side, curves, weights, config):
            return _r(symbol, "BUY", side=side)

        with patch("quorum.roast.read_one", side_effect=fake_read_one):
            result = roast(entries, cfg)

        assert isinstance(result, RoastResult)
        assert [r.symbol for r in result.reads] == ["RELIANCE", "AAPL"]  # order preserved
        # A roast is a throwaway read: it must never write a debate row.
        assert Storage(cfg.db_path).recent_debates() == []


class TestReadOneIntegration:
    def test_numbers_come_from_the_engine(self, golden_fact_pack, tmp_path):
        cfg = QuorumConfig()
        cfg.db_path = tmp_path / "r.db"
        storage = Storage(cfg.db_path)
        curves, weights = storage.latest_curves(), storage.latest_weights()

        with patch("quorum.data.build_fact_pack", return_value=golden_fact_pack):
            read = read_one("NSE", "GOLDEN", 1, curves, weights, cfg)

        assert read.ok
        from quorum.models import Action
        assert read.action in [a.value for a in Action]
        assert read.p_bull is not None and 0.0 < read.p_bull < 1.0
        # read_one must not persist - same throwaway guarantee as roast().
        assert storage.recent_debates() == []
