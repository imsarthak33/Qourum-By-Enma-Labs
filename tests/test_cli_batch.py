"""`quorum batch` (growth plan Horizon 0): the scheduled outcome-accumulation
pass. Basket parsing, isolation between symbols (one failure never aborts the
pass), share flag propagation, and the all-failed exit code."""

from __future__ import annotations

from types import SimpleNamespace

from typer.testing import CliRunner

import quorum.cli as cli
import quorum.orchestrator as orchestrator

runner = CliRunner()


class _FakeVerdict:
    def to_json(self):
        return {"action": "BUY", "p_bull_calibrated": 0.61, "edge": 0.042,
                "calibration_confidence": "low"}


def _fake_debate(status="complete"):
    return SimpleNamespace(
        status=status,
        verdict=None if status == "failed" else _FakeVerdict(),
        degraded=False,
        latency_ms=1,
    )


class TestParseBasket:
    def test_formats_and_comments(self, tmp_path):
        f = tmp_path / "basket.txt"
        f.write_text(
            "﻿"  # BOM, as written by Notepad / PowerShell Out-File
            "# my basket\n"
            "NSE:RELIANCE\n"
            "nasdaq:aapl\n"
            "\n"
            "INFY  # bare symbol defaults to NSE\n",
            encoding="utf-8",
        )
        assert cli._parse_basket(f) == [
            ("NSE", "RELIANCE"),
            ("NASDAQ", "AAPL"),
            ("NSE", "INFY"),
        ]


class TestBatchCommand:
    def _patch(self, monkeypatch, run_debate_impl):
        calls = []
        monkeypatch.setattr(orchestrator, "run_debate", run_debate_impl)
        monkeypatch.setattr(cli, "_resolve_pass", lambda config: calls.append("resolve"))
        return calls

    def test_one_failure_never_aborts_the_pass(self, monkeypatch, tmp_path):
        seen = []

        async def fake_run_debate(symbol, *, exchange, config, on_event, share):
            seen.append((exchange, symbol, share))
            if symbol == "BROKEN":
                raise RuntimeError("feed down")
            return _fake_debate()

        resolve_calls = self._patch(monkeypatch, fake_run_debate)
        f = tmp_path / "b.txt"
        f.write_text("NSE:RELIANCE\nNSE:BROKEN\nNASDAQ:AAPL\n", encoding="utf-8")

        result = runner.invoke(cli.app, ["batch", "--basket", str(f)])
        assert result.exit_code == 0
        assert [s for _, s, _ in seen] == ["RELIANCE", "BROKEN", "AAPL"]
        assert all(share is True for *_, share in seen)  # share defaults on
        assert resolve_calls == ["resolve"]  # resolve still runs after failures
        assert "analyzed 2/3" in result.output

    def test_no_share_flag_propagates(self, monkeypatch, tmp_path):
        seen = []

        async def fake_run_debate(symbol, *, exchange, config, on_event, share):
            seen.append(share)
            return _fake_debate()

        self._patch(monkeypatch, fake_run_debate)
        f = tmp_path / "b.txt"
        f.write_text("NSE:INFY\n", encoding="utf-8")

        result = runner.invoke(cli.app, ["batch", "--basket", str(f), "--no-share"])
        assert result.exit_code == 0
        assert seen == [False]

    def test_all_failed_exits_nonzero(self, monkeypatch, tmp_path):
        async def fake_run_debate(symbol, *, exchange, config, on_event, share):
            return _fake_debate(status="failed")

        self._patch(monkeypatch, fake_run_debate)
        f = tmp_path / "b.txt"
        f.write_text("NSE:INFY\n", encoding="utf-8")

        result = runner.invoke(cli.app, ["batch", "--basket", str(f)])
        assert result.exit_code == 1
