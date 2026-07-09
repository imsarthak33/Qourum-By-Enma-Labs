"""Boundary-locked narration prompts (05_AI_ARCHITECTURE §5).

Each prompt (a) fixes the role, (b) hard-codes the information boundary,
(c) hands the LLM the pre-computed P_i(bull) and its inputs, and (d) demands
the STANCE/CONFIDENCE tail with values GIVEN to the model — the LLM narrates
a number it did not choose, it never originates a stance.
"""

from __future__ import annotations

import json
from typing import Any

from ..models import Stance, stance_from_p

# Which fact-pack keys each agent may see (05 §7) — persisted per opinion for audit.
INFO_BOUNDARIES: dict[str, list[str]] = {
    "technician": ["price", "ohlcv", "indicators", "volume"],
    "fundamentalist": ["fundamentals", "estimates", "mgmt_commentary"],
    "macro": ["flows", "rates", "sector_rotation", "commodities"],
    "devils_advocate": ["prior_opinions", "divergence_stats"],
    "risk": ["volatility", "atr", "catalysts", "sizing"],
}


def _tail(p_bull: float) -> str:
    stance = stance_from_p(p_bull).value
    conf = round(100 * p_bull)
    return (
        f"\nEnd your reply with exactly:\n---\nSTANCE: {stance}\nCONFIDENCE: {conf}\n"
        "Do not change these values — they are computed, not yours to set."
    )


def technician_prompt(symbol: str, feat: dict[str, Any], p_bull: float) -> tuple[str, str]:
    system = (
        "You are The Technician on an adversarial trading council. "
        "You are given a pre-computed regime state (trend/reversion), a mean-reversion "
        "z-score, and a volume z-score — you do NOT decide the stance, it is already "
        "computed. Your job is to explain, in plain English, why the data implies that "
        "stance: cite the regime, the z-score magnitude, and volume observations. "
        "You are FORBIDDEN from discussing fundamentals, valuation, macro, news, or "
        "earnings. If asked about them, state that they are outside your mandate. "
        "Keep it under 90 words."
    )
    user = (
        f"Symbol: {symbol}\nComputed inputs: {json.dumps(feat)}\n"
        f"Computed P(bull) = {p_bull:.2f}. Explain this read." + _tail(p_bull)
    )
    return system, user


def fundamentalist_prompt(symbol: str, feat: dict[str, Any], p_bull: float) -> tuple[str, str]:
    system = (
        "You are The Fundamentalist on an adversarial trading council. "
        "You are given pre-computed sector-standardized factor z-scores "
        "(value, quality, growth, momentum) and a weighted factor score — the stance is "
        "already computed from them, not by you. Explain the factor read in plain English. "
        "You are FORBIDDEN from discussing price charts, technicals, or macro flows. "
        "Keep it under 90 words."
    )
    user = (
        f"Symbol: {symbol}\nComputed factor inputs: {json.dumps(feat)}\n"
        f"Computed P(bull) = {p_bull:.2f}. Explain this read." + _tail(p_bull)
    )
    return system, user


def macro_prompt(symbol: str, feat: dict[str, Any], p_bull: float) -> tuple[str, str]:
    system = (
        "You are The Macro Oracle on an adversarial trading council. "
        "You are given a fitted rolling factor regression (betas to market, currency, "
        "commodity shocks) and today's realized macro shocks — the stance is already "
        "computed. Narrate today's macro shocks and the fitted exposure in plain English. "
        "You are FORBIDDEN from discussing company specifics, earnings, or charts. "
        "Keep it under 90 words."
    )
    user = (
        f"Symbol: {symbol}\nFitted exposures and shocks: {json.dumps(feat)}\n"
        f"Computed P(bull) = {p_bull:.2f}. Explain this read." + _tail(p_bull)
    )
    return system, user


def devils_advocate_prompt(
    symbol: str, feat: dict[str, Any], p_bull: float, prior_opinions: str
) -> tuple[str, str]:
    system = (
        "You are The Devil's Advocate. You are invoked only because a model-disagreement "
        "or positioning-crowding threshold has already fired (see the test result given "
        "to you). Your job is NOT to invent a contrarian angle — it is to explain, using "
        "the Technician/Fundamentalist/Macro opinions provided, why the measured "
        "divergence or crowding is real and what it implies. Prioritise volume anomalies, "
        "insider/promoter behaviour, and second-order effects the others missed. "
        "Under 90 words."
    )
    user = (
        f"Symbol: {symbol}\nDivergence/crowding test result: {json.dumps(feat)}\n"
        f"Prior opinions:\n{prior_opinions}\n"
        f"Computed P(bull) = {p_bull:.2f}. Explain why the divergence matters." + _tail(p_bull)
    )
    return system, user


def risk_prompt(symbol: str, feat: dict[str, Any], p_bull: float) -> tuple[str, str]:
    system = (
        "You are The Risk Ranger on an adversarial trading council. "
        "You are given computed levels (ATR-based stop, target), realized volatility, and "
        "fractional-Kelly sizing inputs — all already computed. Narrate the sizing logic "
        "and the worst-case scenario in plain English. "
        "You are FORBIDDEN from discussing the investment thesis itself. "
        "Keep it under 90 words."
    )
    user = (
        f"Symbol: {symbol}\nComputed risk inputs: {json.dumps(feat)}\n"
        f"Computed P(bull) = {p_bull:.2f}. Explain the risk framing." + _tail(p_bull)
    )
    return system, user


def verdict_prompt(symbol: str, verdict_json: dict[str, Any]) -> tuple[str, str]:
    system = (
        "You are narrating a verdict that has ALREADY been decided by a statistical "
        "model. Do not second-guess, alter, or hedge the numbers given to you — your "
        "only job is to explain them in plain English in <= 60 words. You are given: "
        "action, entry, target, stop, calibrated P(bull), expected value, edge, "
        "and each agent's learned weight this round."
    )
    user = f"Symbol: {symbol}\nComputed verdict: {json.dumps(verdict_json)}\nNarrate it."
    return system, user


PROMPT_BUILDERS = {
    "technician": technician_prompt,
    "fundamentalist": fundamentalist_prompt,
    "macro": macro_prompt,
    "risk": risk_prompt,
}


# Templated fallbacks (06_WORKFLOW §9): if narration fails, the computed
# number still feeds the Chairman with this string in place of prose.
def template_narration(agent: str, p_bull: float, feat: dict[str, Any]) -> str:
    stance = stance_from_p(p_bull).value
    detail = {
        "technician": lambda: f"regime={feat.get('regime')}, z={feat.get('z_signed')}",
        "fundamentalist": lambda: f"factor score={feat.get('score')}",
        "macro": lambda: f"expected daily return={feat.get('expected_daily_return')}",
        "devils_advocate": lambda: f"divergence D={feat.get('D')}",
        "risk": lambda: f"entry={feat.get('entry')}, stop={feat.get('stop')}",
    }.get(agent, lambda: "")()
    return (
        f"[narration unavailable] Computed {stance} at P(bull)={p_bull:.2f} ({detail}). "
        "The number is model-computed; only the explanation is missing."
    )


def template_rationale(verdict_json: dict[str, Any]) -> str:
    a = verdict_json.get("action")
    p = verdict_json.get("p_bull_calibrated")
    edge = verdict_json.get("edge")
    tau = verdict_json.get("hurdle_tau")
    if a in ("BUY", "SELL"):
        return (f"{a}: calibrated P(bull) {p} gives edge {edge}, above the {tau} hurdle. "
                f"Entry {verdict_json.get('entry')}, target {verdict_json.get('target')}, "
                f"stop {verdict_json.get('stop')}.")
    if a == "WAIT":
        return (f"WAIT: edge {edge} is positive but does not clear the {tau} hurdle "
                f"at calibrated P(bull) {p}.")
    return (f"{a}: computed edge {edge} does not clear uncertainty and cost "
            f"(hurdle {tau}) at calibrated P(bull) {p}. No call manufactured.")
