"""Opt-in leaderboard client — Supabase PostgREST (02_TRD §4, 03_DATABASE §4).

Track A's one hosted touchpoint. Instead of a bespoke FastAPI service, the
leaderboard is a Supabase Postgres table exposed through its auto-generated
REST API: the schema's CHECK constraints and RLS policies (append-only,
public-read) are the API contract. The publishable anon key is public by
design — write access is limited to inserting constraint-validated,
anonymous rows.

The payload is deliberately minimal: verdict + outcome only. Never the query
text, never any identity. `FORBIDDEN_FIELDS` is asserted in tests so the
client can never quietly grow a leaky field.
"""

from __future__ import annotations

from typing import Any

import httpx

import quorum

# Fields that must never appear in a submission payload (03 §4: the privacy
# contract). Enforced by tests/test_leaderboard.py.
FORBIDDEN_FIELDS = frozenset({
    "query", "query_text", "user_id", "user", "email", "ip", "ip_address",
    "identity", "debate_id", "reasoning", "rationale",
})

SUBMISSIONS_TABLE = "leaderboard_submissions"


def build_submission(row: dict[str, Any], result: str) -> dict[str, Any]:
    """Shape mirrors leaderboard_submissions (03 §4). Assembled from the
    resolved outcome row; no free-text field exists on purpose."""
    return {
        "symbol": row["symbol"],
        "exchange": row["exchange"],
        "action": row["action"],
        "entry": row["entry"],
        "target": row["target"],
        "stop": row["stop"],
        "p_bull_calibrated": row["p_bull_calibrated"],
        "expected_value": row["expected_value"],
        "edge": row["edge"],
        "agent_weights": row["agent_weights"],
        "result": result,
        "quorum_version": quorum.__version__,
    }


def _headers(anon_key: str) -> dict[str, str]:
    return {
        "apikey": anon_key,
        "Authorization": f"Bearer {anon_key}",
        "Content-Type": "application/json",
    }


def submit(
    supabase_url: str,
    anon_key: str,
    payload: dict[str, Any],
    timeout_s: float = 10.0,
    transport: httpx.BaseTransport | None = None,
) -> bool:
    """Insert one resolved outcome. Best-effort: a leaderboard outage never
    blocks anything local (02_TRD §8)."""
    if not supabase_url or not anon_key:
        return False
    leak = FORBIDDEN_FIELDS & payload.keys()
    if leak:  # defence in depth — the DB schema would reject these anyway
        raise ValueError(f"submission payload contains forbidden fields: {sorted(leak)}")
    try:
        with httpx.Client(transport=transport, timeout=timeout_s) as client:
            r = client.post(
                f"{supabase_url.rstrip('/')}/rest/v1/{SUBMISSIONS_TABLE}",
                headers={**_headers(anon_key), "Prefer": "return=minimal"},
                json=payload,
            )
        return r.status_code < 300
    except httpx.HTTPError:
        return False


def fetch_stats(
    supabase_url: str,
    anon_key: str,
    timeout_s: float = 10.0,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any] | None:
    """Public leaderboard stats: rolling accuracy + calibration quality
    (Brier/log-loss), fetched from the SQL views (03 §4a)."""
    if not supabase_url or not anon_key:
        return None
    base = supabase_url.rstrip("/")
    try:
        with httpx.Client(transport=transport, timeout=timeout_s) as client:
            stats = client.get(f"{base}/rest/v1/leaderboard_stats?select=*",
                               headers=_headers(anon_key))
            quality = client.get(f"{base}/rest/v1/leaderboard_quality?select=*",
                                 headers=_headers(anon_key))
        if stats.status_code >= 300 or quality.status_code >= 300:
            return None
        s = (stats.json() or [{}])[0]
        q = (quality.json() or [{}])[0]
        n = q.get("n_resolved") or 0
        return {
            **s,
            **q,
            # Cold-start transparency (07 §6): sample size gates how the
            # numbers may be presented.
            "calibration_confidence": (
                "high" if n >= 250 else "medium" if n >= 100 else "low"
            ),
        }
    except httpx.HTTPError:
        return None


def fetch_recent_calls(
    supabase_url: str,
    anon_key: str,
    limit: int = 20,
    timeout_s: float = 10.0,
    transport: httpx.BaseTransport | None = None,
) -> list[dict[str, Any]]:
    """Latest public calls from the public_calls view."""
    if not supabase_url or not anon_key:
        return []
    try:
        with httpx.Client(transport=transport, timeout=timeout_s) as client:
            r = client.get(
                f"{supabase_url.rstrip('/')}/rest/v1/public_calls"
                f"?select=*&order=created_at.desc&limit={limit}",
                headers=_headers(anon_key),
            )
        return r.json() if r.status_code < 300 else []
    except httpx.HTTPError:
        return []
