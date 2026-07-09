"""Golden-debate integration (02_TRD §11, 05 §9): fixed fact pack -> expected
structured verdict shape, with stubbed/no providers. Also the degraded path
and the learning loop (outcome -> Hedge update -> calibration refit)."""

from __future__ import annotations

import json

import httpx
import pytest

from quorum.config import QuorumConfig
from quorum.models import Action
from quorum.orchestrator import run_debate
from quorum.providers import OpenAICompatProvider, ProviderRegistry
from quorum.storage import Storage


def _config(tmp_path) -> QuorumConfig:
    cfg = QuorumConfig()
    cfg.db_path = tmp_path / "test.db"
    return cfg


def _stub_registry(narration="The computed read follows from the data.") -> ProviderRegistry:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={
        "choices": [{"message": {"content": narration}}],
        "usage": {"prompt_tokens": 50, "completion_tokens": 30},
    }))
    provider = OpenAICompatProvider("stub", "https://stub/v1", "k", transport=transport)
    profiles = {"free": {
        agent: {"primary": {"p": "stub", "m": "stub-model"}}
        for agent in ("technician", "fundamentalist", "macro", "devils_advocate",
                      "risk", "verdict_narration")
    }}
    return ProviderRegistry(providers={"stub": provider}, profiles=profiles)


class TestGoldenDebate:
    async def test_full_debate_with_stub_providers(self, golden_fact_pack, tmp_path):
        cfg = _config(tmp_path)
        storage = Storage(cfg.db_path)
        events = []
        debate = await run_debate(
            "GOLDEN", config=cfg, registry=_stub_registry(), storage=storage,
            on_event=events.append, fact_pack_override=golden_fact_pack,
        )
        assert debate.status in ("complete", "degraded")
        v = debate.verdict
        assert v is not None
        assert v.action in list(Action)
        assert 0.0 < v.p_bull_calibrated < 1.0
        assert v.hurdle_tau == cfg.hurdle_tau
        assert sum(v.agent_weights.values()) == pytest.approx(1.0, abs=1e-3)
        assert "not investment advice" in v.disclaimer
        # feature_ready fires before any agent narration completes (02_TRD §4.2)
        kinds = [e["event"] for e in events]
        assert kinds.index("feature_ready") < kinds.index("agent_done")
        assert "chairman" in kinds and "done" in kinds

    async def test_debate_without_any_provider_still_computes(self, golden_fact_pack, tmp_path):
        """07 §7: if every provider is down, the Decision Layer still runs —
        only narration degrades to templates."""
        cfg = _config(tmp_path)
        storage = Storage(cfg.db_path)
        registry = ProviderRegistry(providers={}, profiles=cfg.profiles)
        debate = await run_debate(
            "GOLDEN", config=cfg, registry=registry, storage=storage,
            fact_pack_override=golden_fact_pack,
        )
        assert debate.verdict is not None
        assert debate.verdict.p_bull_calibrated is not None
        for op in debate.opinions:
            if op.p_bull_raw is not None:
                assert op.narration_fallback
                assert "narration unavailable" in op.reasoning or op.reasoning

    async def test_missing_price_data_fails_cleanly(self, golden_fact_pack, tmp_path):
        cfg = _config(tmp_path)
        pack, raw = golden_fact_pack
        pack.sources["price"] = "missing"
        debate = await run_debate(
            "GOLDEN", config=cfg, registry=_stub_registry(),
            storage=Storage(cfg.db_path), fact_pack_override=(pack, raw),
        )
        assert debate.status == "failed"
        assert debate.verdict is None

    async def test_persistence_roundtrip(self, golden_fact_pack, tmp_path):
        cfg = _config(tmp_path)
        storage = Storage(cfg.db_path)
        debate = await run_debate(
            "GOLDEN", config=cfg, registry=_stub_registry(), storage=storage,
            fact_pack_override=golden_fact_pack, share=True,
        )
        rows = storage.recent_debates()
        assert len(rows) == 1
        assert rows[0]["symbol"] == "GOLDEN"
        assert rows[0]["share"] == 1
        p_cals = storage.calibrated_p_for_debate(debate.debate_id)
        assert len(p_cals) >= 3  # at least the responding primaries

    async def test_verdict_reproducible_from_same_inputs(self, golden_fact_pack, tmp_path):
        """02_TRD §5: a verdict must be re-derivable from its stored inputs."""
        cfg = _config(tmp_path)
        d1 = await run_debate("GOLDEN", config=cfg, registry=_stub_registry(),
                              storage=Storage(cfg.db_path),
                              fact_pack_override=golden_fact_pack)
        d2 = await run_debate("GOLDEN", config=cfg, registry=_stub_registry(),
                              storage=Storage(tmp_path / "other.db"),
                              fact_pack_override=golden_fact_pack)
        v1, v2 = d1.verdict.to_json(), d2.verdict.to_json()
        v1.pop("rationale"), v2.pop("rationale")  # narration text may vary; numbers may not
        assert v1 == v2


class TestLearningLoop:
    async def test_hedge_update_after_resolution(self, golden_fact_pack, tmp_path):
        from quorum.outcomes import meta_learner_update

        cfg = _config(tmp_path)
        storage = Storage(cfg.db_path)
        debate = await run_debate("GOLDEN", config=cfg, registry=_stub_registry(),
                                  storage=storage, fact_pack_override=golden_fact_pack)
        before = storage.latest_weights()
        after = meta_learner_update(storage, cfg, debate.debate_id,
                                    outcome_bull_correct=True)
        assert sum(after.values()) == pytest.approx(1.0, abs=1e-6)
        assert after != before  # something moved

    async def test_calibration_refit_needs_samples(self, tmp_path):
        from quorum.outcomes import calibration_refit

        storage = Storage(tmp_path / "t.db")
        fitted = calibration_refit(storage)
        assert fitted == {}  # empty outcome log -> identity seeds stay
