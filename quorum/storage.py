"""Local-first persistence — SQLite by default (03_DATABASE, Track A schema).

No users/auth tables: single implicit user, the debate is the aggregate root.
Prices are stored as TEXT-rendered decimals in SQLite (its NUMERIC affinity
is best-effort); every numeric the user sees is rounded at render time.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import AGENTS, DebateResult, Verdict, utcnow
from .quant.calibration import CalibrationCurve
from .quant.weights import uniform_weights

SCHEMA = """
CREATE TABLE IF NOT EXISTS debates (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    query TEXT,
    provider_profile TEXT,
    status TEXT NOT NULL,
    degraded INTEGER DEFAULT 0,
    share INTEGER DEFAULT 0,
    latency_ms INTEGER,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_debates_symbol ON debates(symbol, created_at DESC);

CREATE TABLE IF NOT EXISTS debate_facts (
    debate_id TEXT PRIMARY KEY REFERENCES debates(id),
    fact_pack TEXT NOT NULL,
    quant_features TEXT NOT NULL,
    calibration_version TEXT,
    sources TEXT,
    captured_at TEXT
);

CREATE TABLE IF NOT EXISTS agent_opinions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    debate_id TEXT NOT NULL REFERENCES debates(id),
    agent TEXT NOT NULL,
    p_bull_raw REAL,
    p_bull_calibrated REAL,
    ensemble_weight REAL,
    provider TEXT,
    model TEXT,
    stance TEXT,
    confidence INTEGER,
    reasoning TEXT,
    info_boundary TEXT,
    triggered INTEGER DEFAULT 1,
    narration_fallback INTEGER DEFAULT 0,
    tokens_in INTEGER,
    tokens_out INTEGER,
    latency_ms INTEGER,
    fell_back INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_opinions_debate ON agent_opinions(debate_id);

CREATE TABLE IF NOT EXISTS verdicts (
    debate_id TEXT PRIMARY KEY REFERENCES debates(id),
    action TEXT NOT NULL,
    entry REAL, target REAL, stop REAL,
    risk_reward REAL,
    p_bull_calibrated REAL,
    expected_value REAL,
    edge REAL,
    hurdle_tau REAL,
    kelly_fraction REAL,
    position_size_pct REAL,
    agent_weights TEXT,
    calibration_confidence TEXT,
    rationale TEXT,
    narration_provider TEXT,
    narration_model TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS verdict_outcomes (
    verdict_debate_id TEXT PRIMARY KEY REFERENCES verdicts(debate_id),
    resolved INTEGER DEFAULT 0,
    result TEXT,
    resolved_price REAL,
    resolved_at TEXT,
    window_days INTEGER DEFAULT 30,
    correct INTEGER,
    last_checked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_outcomes_pending
    ON verdict_outcomes(resolved, last_checked_at);

CREATE TABLE IF NOT EXISTS agent_calibration_curves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    regime TEXT NOT NULL DEFAULT 'all',
    curve TEXT NOT NULL,
    n_samples INTEGER DEFAULT 0,
    fit_at TEXT NOT NULL,
    version TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_calib ON agent_calibration_curves(agent, regime, fit_at DESC);

CREATE TABLE IF NOT EXISTS agent_weight_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    debate_id TEXT REFERENCES debates(id),
    weights TEXT NOT NULL,
    eta REAL,
    updated_at TEXT NOT NULL
);
"""


class Storage:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # ── debates ────────────────────────────────────────────────────────────

    def save_debate(self, d: DebateResult) -> None:
        c = self._conn
        c.execute(
            """INSERT OR REPLACE INTO debates
               (id, symbol, exchange, query, provider_profile, status, degraded,
                share, latency_ms, created_at, completed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (d.debate_id, d.symbol, d.exchange, d.query, d.provider_profile,
             d.status, int(d.degraded), int(d.share), d.latency_ms,
             d.created_at.isoformat(), utcnow().isoformat()),
        )
        if d.fact_pack is not None:
            c.execute(
                """INSERT OR REPLACE INTO debate_facts
                   (debate_id, fact_pack, quant_features, calibration_version,
                    sources, captured_at)
                   VALUES (?,?,?,?,?,?)""",
                (d.debate_id, json.dumps(d.fact_pack.to_json()),
                 json.dumps(d.quant_features), d.calibration_version,
                 json.dumps(d.fact_pack.sources),
                 d.fact_pack.captured_at.isoformat()),
            )
        for op in d.opinions:
            c.execute(
                """INSERT INTO agent_opinions
                   (debate_id, agent, p_bull_raw, p_bull_calibrated, ensemble_weight,
                    provider, model, stance, confidence, reasoning, info_boundary,
                    triggered, narration_fallback, tokens_in, tokens_out, latency_ms,
                    fell_back, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (d.debate_id, op.agent, op.p_bull_raw, op.p_bull_calibrated,
                 op.ensemble_weight, op.provider, op.model,
                 op.stance.value if op.stance else None, op.confidence,
                 op.reasoning, json.dumps(op.info_boundary), int(op.triggered),
                 int(op.narration_fallback), op.tokens_in, op.tokens_out,
                 op.latency_ms, int(op.fell_back), utcnow().isoformat()),
            )
        if d.verdict is not None:
            v = d.verdict
            c.execute(
                """INSERT OR REPLACE INTO verdicts
                   (debate_id, action, entry, target, stop, risk_reward,
                    p_bull_calibrated, expected_value, edge, hurdle_tau,
                    kelly_fraction, position_size_pct, agent_weights,
                    calibration_confidence, rationale, narration_provider,
                    narration_model, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (d.debate_id, v.action.value, v.entry, v.target, v.stop,
                 v.risk_reward, v.p_bull_calibrated, v.expected_value, v.edge,
                 v.hurdle_tau, v.kelly_fraction, v.position_size_pct,
                 json.dumps(v.agent_weights), v.calibration_confidence,
                 v.rationale, v.narration_provider, v.narration_model,
                 utcnow().isoformat()),
            )
            # Enqueue for outcome tracking only when there are levels to resolve.
            if v.entry is not None and v.action.value in ("BUY", "SELL"):
                c.execute(
                    """INSERT OR IGNORE INTO verdict_outcomes
                       (verdict_debate_id, resolved, window_days)
                       VALUES (?, 0, 30)""",
                    (d.debate_id,),
                )
        c.commit()

    def recent_debates(self, limit: int = 20) -> list[sqlite3.Row]:
        return self._conn.execute(
            """SELECT d.*, v.action, v.p_bull_calibrated AS p_bull, v.edge
               FROM debates d LEFT JOIN verdicts v ON v.debate_id = d.id
               ORDER BY d.created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()

    # ── outcomes (06_WORKFLOW §5, idempotent worker) ───────────────────────

    def pending_outcomes(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            """SELECT o.verdict_debate_id, o.window_days, o.last_checked_at,
                      d.symbol, d.exchange, d.created_at, d.share,
                      v.action, v.entry, v.target, v.stop, v.p_bull_calibrated,
                      v.expected_value, v.edge, v.agent_weights
               FROM verdict_outcomes o
               JOIN verdicts v ON v.debate_id = o.verdict_debate_id
               JOIN debates d ON d.id = o.verdict_debate_id
               WHERE o.resolved = 0"""
        ).fetchall()

    def resolve_outcome(
        self, debate_id: str, result: str, resolved_price: float | None, correct: bool | None
    ) -> None:
        self._conn.execute(
            """UPDATE verdict_outcomes
               SET resolved = 1, result = ?, resolved_price = ?, resolved_at = ?,
                   correct = ?, last_checked_at = ?
               WHERE verdict_debate_id = ?""",
            (result, resolved_price, utcnow().isoformat(),
             None if correct is None else int(correct),
             utcnow().isoformat(), debate_id),
        )
        self._conn.commit()

    def touch_outcome(self, debate_id: str) -> None:
        self._conn.execute(
            "UPDATE verdict_outcomes SET last_checked_at = ? WHERE verdict_debate_id = ?",
            (utcnow().isoformat(), debate_id),
        )
        self._conn.commit()

    def resolved_outcomes_for_agent(self, agent: str) -> list[tuple[float, int]]:
        """(p_bull_raw, outcome) pairs feeding the weekly isotonic refit."""
        rows = self._conn.execute(
            """SELECT ao.p_bull_raw, o.correct
               FROM agent_opinions ao
               JOIN verdict_outcomes o ON o.verdict_debate_id = ao.debate_id
               WHERE ao.agent = ? AND o.resolved = 1 AND o.correct IS NOT NULL
                     AND ao.p_bull_raw IS NOT NULL""",
            (agent,),
        ).fetchall()
        return [(r["p_bull_raw"], r["correct"]) for r in rows]

    # ── calibration curves & hedge weights ─────────────────────────────────

    def latest_curves(self) -> dict[str, CalibrationCurve]:
        curves: dict[str, CalibrationCurve] = {}
        for agent in AGENTS:
            row = self._conn.execute(
                """SELECT * FROM agent_calibration_curves
                   WHERE agent = ? ORDER BY fit_at DESC LIMIT 1""",
                (agent,),
            ).fetchone()
            if row:
                curves[agent] = CalibrationCurve.from_json(
                    {"agent": agent, "regime": row["regime"],
                     "curve": json.loads(row["curve"]),
                     "n_samples": row["n_samples"], "version": row["version"]}
                )
            else:
                curves[agent] = CalibrationCurve.identity(agent)
        return curves

    def save_curve(self, curve: CalibrationCurve) -> None:
        data = curve.to_json()
        self._conn.execute(
            """INSERT INTO agent_calibration_curves
               (agent, regime, curve, n_samples, fit_at, version)
               VALUES (?,?,?,?,?,?)""",
            (curve.agent, curve.regime, json.dumps(data["curve"]),
             curve.n_samples, utcnow().isoformat(), curve.version),
        )
        self._conn.commit()

    def latest_weights(self) -> dict[str, float]:
        row = self._conn.execute(
            "SELECT weights FROM agent_weight_history ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return json.loads(row["weights"]) if row else uniform_weights()

    def save_weights(self, weights: dict[str, float], eta: float, debate_id: str | None) -> None:
        self._conn.execute(
            """INSERT INTO agent_weight_history (debate_id, weights, eta, updated_at)
               VALUES (?,?,?,?)""",
            (debate_id, json.dumps(weights), eta, utcnow().isoformat()),
        )
        self._conn.commit()

    def calibrated_p_for_debate(self, debate_id: str) -> dict[str, float]:
        rows = self._conn.execute(
            """SELECT agent, p_bull_calibrated FROM agent_opinions
               WHERE debate_id = ? AND p_bull_calibrated IS NOT NULL""",
            (debate_id,),
        ).fetchall()
        return {r["agent"]: r["p_bull_calibrated"] for r in rows}

    # ── track record (07 §5) ───────────────────────────────────────────────

    def track_record(self, window_days: int = 90) -> dict:
        cutoff = (utcnow() - timedelta(days=window_days)).isoformat()
        row = self._conn.execute(
            """SELECT
                 COUNT(*) FILTER (WHERE o.correct = 1) AS hits,
                 COUNT(*) AS resolved,
                 AVG((v.p_bull_calibrated - o.correct) * (v.p_bull_calibrated - o.correct))
                    AS brier
               FROM verdict_outcomes o
               JOIN verdicts v ON v.debate_id = o.verdict_debate_id
               JOIN debates d ON d.id = o.verdict_debate_id
               WHERE o.resolved = 1 AND o.correct IS NOT NULL AND d.created_at > ?""",
            (cutoff,),
        ).fetchone()
        resolved = row["resolved"] or 0
        return {
            "resolved": resolved,
            "hits": row["hits"] or 0,
            "accuracy_pct": round(100.0 * (row["hits"] or 0) / resolved, 1) if resolved else None,
            "brier_score": round(row["brier"], 4) if row["brier"] is not None else None,
            "calibration_confidence": (
                "high" if resolved >= 250 else "medium" if resolved >= 100 else "low"
            ),
        }

    def close(self) -> None:
        self._conn.close()
