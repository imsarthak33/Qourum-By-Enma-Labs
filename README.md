# Quorum — *Don't trade alone.*

**The verdict is decided by math, not another LLM.**

Quorum is an open-source, auditable, provider-agnostic AI debate engine for stock analysis. A Quant Analyst orchestrator briefs five role-locked agents — **Technician, Fundamentalist, Macro Oracle, Devil's Advocate, Risk Ranger** — each backed by a real statistical model (regime-switching HMMs, cross-sectional factor scores, rolling macro betas, divergence/crowding tests) and each narrated by a *different LLM from a different provider*.

Here's the part every other "AI agents debate a stock" repo doesn't do: **the verdict isn't decided by another LLM.** A deterministic **Chairman** — pure math, zero token cost — calibrates each agent's output against its own historical accuracy (isotonic regression), combines them via a logarithmic opinion pool with weights learned online from real outcomes (Hedge / multiplicative weights), and issues a verdict only when the computed expected value clears a hurdle. When it doesn't, Quorum says **NO_CALL** instead of manufacturing a confident-sounding answer.

```
$ quorum analyze TATAMOTORS

Convening the council on TATAMOTORS…
  The Technician: computed P(bull) = 0.68
  The Fundamentalist: computed P(bull) = 0.61
  The Macro Oracle: computed P(bull) = 0.47
  The Risk Ranger: computed P(bull) = 0.52
  The Devil's Advocate: divergence test did not fire — silent this round

  … narrations stream in from Groq / Gemini / OpenRouter …

╭───────────── The Chairman — computed verdict ─────────────╮
│ WAIT                                                       │
│ P(bull) 0.5892 · EV 4.1 · edge 0.081 vs hurdle 0.15        │
│ weights: Technician 0.25 · Fundamentalist 0.25 · …         │
│ calibration confidence: low                                │
│                                                            │
│ AI analysis, not investment advice.                        │
╰────────────────────────────────────────────────────────────╯
```

## Quickstart (under a minute)

```bash
pip install -e .
export GROQ_API_KEY=gsk_...        # any ONE free key is enough to start
quorum analyze TATAMOTORS
```

Supported out of the box (all free tiers work): **Groq, Google AI Studio (Gemini), OpenRouter, NVIDIA NIM, Together, Fireworks**, plus any OpenAI-compatible endpoint via config. No account, no hosted backend, no telemetry — your keys, your machine, your data.

No key at all? The debate still runs: every probability, level, and the verdict are computed by the deterministic Quant Core (it has zero provider dependency) — you just get templated explanations instead of prose.

## Design tenets

1. **Math decides, LLMs narrate.** Every probability, weight, and position size is computed by a calibrated statistical model. LLMs explain numbers they did not choose — they never originate a stance, probability, or decision.
2. **Forced epistemic separation.** Agents disagree because they are *denied* each other's information (the Technician never sees fundamentals; the Fundamentalist never sees the chart) — not because they were prompted to "debate."
3. **Provider-agnostic by construction.** Every agent binding is a config entry with cross-vendor fallbacks; a single-vendor outage can't kill the debate.
4. **Auditable or it didn't happen.** Every debate persists its fact pack + computed features to local SQLite, so any verdict is re-derivable byte-for-byte. Calibration quality (Brier score) is tracked, not asserted.
5. **Honest about cold start.** Calibration curves seed to identity and ensemble weights to uniform; a `calibration_confidence` indicator is shown until enough outcomes resolve (~200–300 per agent). Early numbers are never presented with false precision.

## The council

| Agent | Sees only | Quant model | LLM's job |
|-------|-----------|-------------|-----------|
| The Technician | OHLCV, indicators, volume | 2-state HMM regime + mean-reversion z-score → logistic | narrate the regime + z-score |
| The Fundamentalist | financials, estimates | cross-sectional factor score (value/quality/growth/momentum) | narrate the factor read |
| The Macro Oracle | flows, rates, sector rotation | rolling 60-day factor regression + sector PCA | narrate today's macro shocks |
| The Devil's Advocate | prior opinions + divergence stats | model-disagreement & crowding threshold test | narrate *why* a fired divergence matters |
| The Risk Ranger | volatility, ATR, catalysts | ATR stop + fractional Kelly + CVaR cap | narrate sizing & worst case |
| **The Chairman** | all computed probabilities | **deterministic**: calibrate → log opinion pool → Hedge weights → EV hurdle | *none — pure function, not an LLM call* |

## CLI

```bash
quorum analyze RELIANCE            # convene the council
quorum analyze INFY --json         # machine-readable verdict
quorum analyze TCS --share         # opt in: submit the RESOLVED OUTCOME (never
                                   # your query or identity) to the public leaderboard
quorum history                     # local debate history
quorum resolve                     # check open verdicts vs price; updates Hedge weights
quorum calibrate                   # weekly isotonic refit from your outcome log
quorum leaderboard                 # public community track record (accuracy + Brier)
quorum init                        # write an example quorum.yaml
```

## Library

```python
import asyncio, quorum

result = asyncio.run(quorum.analyze("TATAMOTORS"))
print(result.verdict.to_json())          # every number computed, rationale narrated
```

## Configuration

`quorum init` writes an example `quorum.yaml`. Keys are read from env vars first (`GROQ_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `NVIDIA_API_KEY`, `TOGETHER_API_KEY`, `FIREWORKS_API_KEY`). Any OpenAI-compatible endpoint (e.g. self-hosted NIM) is a two-line config entry. Agent→model bindings, the EV hurdle `tau`, the Kelly fraction `lambda`, and the ATR stop multiple are all tunable.

## The public leaderboard

The leaderboard is a Supabase Postgres table exposed through its auto-generated REST API — no bespoke server to run. The schema *is* the privacy contract: `leaderboard_submissions` has no column capable of holding your query text or identity (every text column is CHECK-constrained to an enum or strict pattern), the API is append-only for the public key, and a CI test fails if the migration ever grows a leaky column. Submissions happen only when you pass `--share`, and only after an outcome resolves. The migration lives in [supabase/migrations/](supabase/migrations).

## How the learning loop works

1. Each verdict with levels is enqueued for outcome tracking (30-day window).
2. `quorum resolve` walks price history: target-hit / stop-hit / expired-open.
3. Every resolution updates the **Hedge weights** (agents that were confident-and-wrong lose weight, per-regime, with a provable regret bound) and feeds the **isotonic calibration** refit (`quorum calibrate`).
4. Accuracy, Brier score, and calibration confidence are computed from real outcomes only — `quorum resolve` prints your rolling track record.

## Development

```bash
pip install -e ".[dev,hmm]"
pytest
```

The Chairman and every feature model are pure functions with unit tests (calibration monotonicity, Hedge normalisation, golden-debate regression). Provider adapters are tested against mocked HTTP.

## Extending

- **New agent** → add a feature model (`quorum/quant/`), a boundary-locked prompt (`quorum/agents/prompts.py`), and an entry in the calibration/weight tables. Aggregation and decision layers are untouched.
- **New provider** → a config entry if it's OpenAI-compatible (most are); otherwise one small adapter class implementing `CouncilProvider`.
- **New market** → new data adapters and factor universe; the HMM/regression/PCA machinery is market-agnostic.

## Status & scope

`v0.1` — Track A (open-source, self-hosted CLI/library). The hosted leaderboard service, browser extension, and broker overlay are deliberately out of scope until this core earns traction. Research mode only: Quorum never places, prepares, or pre-fills an order.

**Every verdict is AI analysis, not investment advice.** Quorum is not SEBI-registered and does not provide investment advice; you run it yourself, on your own keys, at your own risk.
