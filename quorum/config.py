"""Configuration: provider registry bootstrap + agent bindings.

Track A rule (02_TRD §3): provider secrets live in the user's own local
config/env — there is no client/server boundary. Adding a provider requires
only a config entry (all listed vendors are OpenAI-compatible).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .providers import OpenAICompatProvider, ProviderRegistry

# Known OpenAI-compatible vendors (02_TRD §3 / 05_AI_ARCHITECTURE §3.3).
KNOWN_PROVIDERS: dict[str, dict[str, Any]] = {
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "env_key": "OPENROUTER_API_KEY",
        "headers": {"HTTP-Referer": "https://github.com/enma-labs/quorum", "X-Title": "Quorum"},
    },
    "nim": {
        "base_url": "https://integrate.api.nvidia.com/v1",
        "env_key": "NVIDIA_API_KEY",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "env_key": "GROQ_API_KEY",
    },
    "together": {
        "base_url": "https://api.together.xyz/v1",
        "env_key": "TOGETHER_API_KEY",
    },
    "fireworks": {
        "base_url": "https://api.fireworks.ai/inference/v1",
        "env_key": "FIREWORKS_API_KEY",
    },
    # Google AI Studio exposes an OpenAI-compatible endpoint, so the universal
    # adapter covers it too — no bespoke GeminiProvider needed (05 §3.3).
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "env_key": "GEMINI_API_KEY",
    },
}

# Default agent → (provider, model) bindings with cross-vendor fallbacks
# (05_AI_ARCHITECTURE §3.4). Users override these in quorum.yaml.
DEFAULT_PROFILES: dict[str, dict[str, Any]] = {
    "free": {
        "technician": {
            "primary": {"p": "groq", "m": "llama-3.3-70b-versatile"},
            "fallbacks": [{"p": "openrouter", "m": "meta-llama/llama-3.1-70b-instruct"}],
        },
        "fundamentalist": {
            "primary": {"p": "gemini", "m": "gemini-2.0-flash"},
            "fallbacks": [{"p": "openrouter", "m": "google/gemini-2.0-flash-001"}],
        },
        "macro": {
            "primary": {"p": "openrouter", "m": "mistralai/mixtral-8x7b-instruct"},
            "fallbacks": [{"p": "together", "m": "mistralai/Mixtral-8x7B-Instruct-v0.1"},
                          {"p": "groq", "m": "llama-3.3-70b-versatile"}],
        },
        "devils_advocate": {
            "primary": {"p": "openrouter", "m": "cohere/command-r-plus"},
            "fallbacks": [{"p": "nim", "m": "meta/llama-3.1-70b-instruct"},
                          {"p": "groq", "m": "llama-3.3-70b-versatile"}],
        },
        "risk": {
            "primary": {"p": "groq", "m": "llama-3.3-70b-versatile"},
            "fallbacks": [{"p": "openrouter", "m": "meta-llama/llama-3.1-70b-instruct"}],
        },
        # The Chairman decides via pure math (07 §3); this binding is ONLY for
        # the <=60-word verdict narration call. Fast/cheap is fine — it isn't
        # deciding anything.
        "verdict_narration": {
            "primary": {"p": "groq", "m": "llama-3.1-8b-instant"},
            "fallbacks": [{"p": "gemini", "m": "gemini-2.0-flash"},
                          {"p": "openrouter", "m": "openai/gpt-4o-mini"}],
        },
    },
}


@dataclass
class QuorumConfig:
    profile: str = "free"
    profiles: dict[str, Any] = field(default_factory=lambda: DEFAULT_PROFILES)
    api_keys: dict[str, str] = field(default_factory=dict)     # provider name -> key
    custom_providers: dict[str, Any] = field(default_factory=dict)  # name -> {base_url, api_key?}
    db_path: Path = field(default_factory=lambda: Path.home() / ".quorum" / "quorum.db")
    hurdle_tau: float = 0.15            # EV/edge hurdle (07 §3.4)
    hedge_eta: float = 0.1              # Hedge learning rate (07 §3.3)
    kelly_lambda: float = 0.35          # fractional Kelly multiplier (07 §2.5)
    atr_stop_k: float = 1.8             # stop distance in ATRs (07 §2.5)
    outcome_window_days: int = 30
    # Leaderboard = Supabase Postgres via its auto REST API (03_DATABASE §4).
    # The publishable key is public by design (RLS limits it to append-only
    # inserts + reads of anonymised views) — safe to ship as a default.
    supabase_url: str = "https://zeyntnboekfhekjwshne.supabase.co"
    supabase_anon_key: str = "sb_publishable_Ybest1M-BeOi25UHTAymNQ_wY_mGqNo"

    @staticmethod
    def config_paths() -> list[Path]:
        return [
            Path.cwd() / "quorum.yaml",
            Path.home() / ".quorum" / "config.yaml",
        ]

    @classmethod
    def load(cls, path: Path | None = None) -> "QuorumConfig":
        cfg = cls()
        data: dict[str, Any] = {}
        candidates = [path] if path else cls.config_paths()
        for p in candidates:
            if p and p.exists():
                data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                break
        cfg.profile = data.get("profile", cfg.profile)
        if "profiles" in data:
            merged = dict(DEFAULT_PROFILES)
            merged.update(data["profiles"])
            cfg.profiles = merged
        cfg.api_keys = data.get("api_keys", {}) or {}
        cfg.custom_providers = data.get("providers", {}) or {}
        if "db_path" in data:
            cfg.db_path = Path(data["db_path"]).expanduser()
        for knob in ("hurdle_tau", "hedge_eta", "kelly_lambda", "atr_stop_k",
                     "outcome_window_days", "supabase_url", "supabase_anon_key"):
            if knob in data:
                setattr(cfg, knob, data[knob])
        # Env vars win, same policy as provider keys.
        cfg.supabase_url = os.environ.get("QUORUM_SUPABASE_URL", cfg.supabase_url)
        cfg.supabase_anon_key = os.environ.get("QUORUM_SUPABASE_ANON_KEY", cfg.supabase_anon_key)
        return cfg

    def resolve_key(self, provider_name: str) -> str | None:
        """Env var wins over config file so keys never need to be written to disk."""
        spec = KNOWN_PROVIDERS.get(provider_name, {})
        env_key = spec.get("env_key")
        if env_key and os.environ.get(env_key):
            return os.environ[env_key]
        return self.api_keys.get(provider_name)

    def build_registry(self) -> ProviderRegistry:
        """Instantiate every provider the user actually has a key for.

        A missing key simply drops that vendor from every fallback chain — the
        debate still runs; agents without any usable chain degrade to templated
        narration (numbers are unaffected: the Quant Core has no provider
        dependency at all, 07 §7).
        """
        providers: dict[str, OpenAICompatProvider] = {}
        for name, spec in KNOWN_PROVIDERS.items():
            key = self.resolve_key(name)
            if key:
                providers[name] = OpenAICompatProvider(
                    name=name,
                    base_url=spec["base_url"],
                    api_key=key,
                    default_headers=spec.get("headers"),
                )
        for name, spec in self.custom_providers.items():
            key = spec.get("api_key") or os.environ.get(spec.get("env_key", ""), "")
            if spec.get("base_url"):
                providers[name] = OpenAICompatProvider(
                    name=name, base_url=spec["base_url"], api_key=key or "none"
                )
        return ProviderRegistry(providers=providers, profiles=self.profiles)


EXAMPLE_CONFIG = """\
# Quorum configuration — place at ./quorum.yaml or ~/.quorum/config.yaml
# API keys are read from environment variables first:
#   GROQ_API_KEY, GEMINI_API_KEY, OPENROUTER_API_KEY, NVIDIA_API_KEY,
#   TOGETHER_API_KEY, FIREWORKS_API_KEY
profile: free

# api_keys:            # optional — env vars are preferred
#   groq: gsk_...

# providers:           # any extra OpenAI-compatible endpoint
#   my_nim:
#     base_url: http://localhost:8000/v1
#     api_key: none

# profiles:            # override agent -> model bindings
#   free:
#     technician:
#       primary: {p: groq, m: llama-3.3-70b-versatile}
#       fallbacks: [{p: openrouter, m: meta-llama/llama-3.1-70b-instruct}]

hurdle_tau: 0.15       # EV edge hurdle (07_QUANT_CORE §3.4)
kelly_lambda: 0.35     # fractional Kelly (07_QUANT_CORE §2.5)
outcome_window_days: 30

# Public leaderboard (opt-in via --share). The anon key is publishable by
# design. Also settable via QUORUM_SUPABASE_URL / QUORUM_SUPABASE_ANON_KEY.
# supabase_url: https://<project-ref>.supabase.co
# supabase_anon_key: sb_publishable_...
"""
