"""Leaderboard privacy contract + Supabase client (03_DATABASE §4).

Doc 03 §4: 'A CI/unit test MUST assert this table has no column capable of
holding free text or an identifier — the schema itself is the enforcement
mechanism.' These tests pin both the migration SQL and the client payload.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest

from quorum.leaderboard import (
    FORBIDDEN_FIELDS,
    build_submission,
    fetch_stats,
    submit,
)

MIGRATION = (Path(__file__).parent.parent / "supabase" / "migrations"
             / "0001_leaderboard_schema.sql")

ROW = {
    "symbol": "RELIANCE", "exchange": "NSE", "action": "BUY",
    "entry": 1279.8, "target": 1356.27, "stop": 1241.56,
    "p_bull_calibrated": 0.61, "expected_value": 26.4, "edge": 0.31,
    "agent_weights": json.dumps({"technician": 0.25}),
}


class TestPrivacyContract:
    def test_migration_has_no_identity_or_freetext_columns(self):
        sql = MIGRATION.read_text(encoding="utf-8").lower()
        # Strip comments — the contract is about actual columns, not prose.
        code = "\n".join(line for line in sql.splitlines()
                         if not line.strip().startswith("--"))
        for forbidden in ("query", "user_id", "email", "ip_address",
                          "identity", "handle", "display_name"):
            assert forbidden not in code, f"forbidden column-ish token: {forbidden}"

    def test_every_text_column_is_constrained(self):
        """Any `text` column must be locked by a CHECK on the same table —
        no unconstrained free-text column can exist."""
        sql = MIGRATION.read_text(encoding="utf-8")
        table = sql.split("create table", 1)[1].split(");", 1)[0]
        text_cols = re.findall(r"^\s*(\w+)\s+text\b", table, flags=re.MULTILINE)
        assert text_cols, "sanity: table should have text columns"
        for col in text_cols:
            col_def = table.split(col, 1)[1].split(",\n", 1)[0]
            assert "check" in col_def.lower(), f"text column '{col}' has no CHECK"

    def test_payload_contains_no_forbidden_fields(self):
        payload = build_submission(ROW, "target_hit")
        assert not (FORBIDDEN_FIELDS & payload.keys())
        assert payload["result"] == "target_hit"
        assert "quorum_version" in payload

    def test_submit_refuses_leaky_payload(self):
        with pytest.raises(ValueError, match="forbidden"):
            submit("https://x.supabase.co", "key",
                   {**build_submission(ROW, "void"), "query": "should I buy?"})


class TestSupabaseClient:
    def test_submit_posts_to_postgrest(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["apikey"] = request.headers.get("apikey")
            seen["body"] = json.loads(request.content)
            return httpx.Response(201)

        ok = submit("https://proj.supabase.co", "anon-key",
                    build_submission(ROW, "target_hit"),
                    transport=httpx.MockTransport(handler))
        assert ok
        assert seen["url"].endswith("/rest/v1/leaderboard_submissions")
        assert seen["apikey"] == "anon-key"
        assert seen["body"]["symbol"] == "RELIANCE"

    def test_submit_without_config_is_noop(self):
        assert submit("", "", build_submission(ROW, "void")) is False

    def test_submit_survives_outage(self):
        def handler(_: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down")

        ok = submit("https://proj.supabase.co", "k",
                    build_submission(ROW, "void"),
                    transport=httpx.MockTransport(handler))
        assert ok is False  # never raises — local runs are unaffected

    def test_fetch_stats_merges_views_and_flags_cold_start(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if "leaderboard_stats" in str(request.url):
                return httpx.Response(200, json=[{"hits": 6, "resolved": 10,
                                                  "accuracy_pct": 60.0}])
            return httpx.Response(200, json=[{"brier_score": 0.21,
                                              "log_loss": 0.62, "n_resolved": 10}])

        stats = fetch_stats("https://proj.supabase.co", "k",
                            transport=httpx.MockTransport(handler))
        assert stats["accuracy_pct"] == 60.0
        assert stats["brier_score"] == 0.21
        assert stats["calibration_confidence"] == "low"  # 10 << 250 (07 §6)
