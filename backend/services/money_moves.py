"""The money-move engine — hybrid: a DETERMINISTIC decision picks the single most important move this
cycle and owns every number; an optional AI layer only phrases it (never computes it).

This replaces the old per-tab hardcoded advice. `decide()` is a pure function over already-computed
facts (surplus context, recovery, the payoff plan's first step), so it's fast, testable, and general —
you add a new situation by adding a branch + a detector, not by editing UI copy. `narrate()` turns the
chosen move's EXACT facts into one short sentence, falling back to the deterministic headline when no
AI is configured or it errors — so the number is ALWAYS right and the UI never blocks on the model.
"""

from __future__ import annotations


def _money(n: float, symbol: str) -> str:
    return f"{symbol}{round(n):,}"


def for_household(db, household_id: int | None) -> dict | None:
    """The single money move for a household — the ONE place any tab (Debt, Cockpit, …) asks 'what
    should I do this cycle?'. Assembles the computed facts once (debt surplus/recovery/first-step) and
    decides. Deterministic; narration is added by the caller via narrate()."""
    from api.debt import compute_debt
    return compute_debt(db, household_id).get("strategies", {}).get("move")


def decide(sc: dict, recovery: dict | None, first_step: dict | None,
           symbol: str | None = None) -> dict:
    """Pick THE move for this cycle from computed facts. Priority: fix a real cash shortfall → attack
    debt with genuine spare → hold when merely under the savings target → otherwise on track.
    Returns {kind, headline (deterministic, safe fallback), facts (exact numbers), recovery, target}."""
    from services.money_format import DEFAULT_SYMBOL
    _inr = lambda v: _money(v, symbol or DEFAULT_SYMBOL)  # noqa: E731
    overspend = round(sc.get("overspend", 0) or 0)
    actionable = round(sc.get("actionable", 0) or 0)
    over_budget = round(sc.get("over_budget", 0) or 0)
    surplus = round(sc.get("surplus", 0) or 0)

    if not sc.get("data_ok", True):
        return {"kind": "unknown", "headline": "Set up your Plan (income + budget) to get a money move here.",
                "facts": {}, "recovery": None, "target": None}

    # 1) Real cash shortfall — you've spent beyond your free cash. Cover it (no new loan), don't prepay.
    if overspend > 0:
        covered = bool(recovery and recovery.get("covered"))
        headline = (f"You’re {_inr(overspend)} short of your free cash this cycle — don’t prepay debt; "
                    f"cover it from real cash first" + (", no loan needed." if covered else "."))
        return {"kind": "cover_overspend", "headline": headline,
                "facts": {"overspend": overspend, "covered": covered,
                          "non_loan_total": round((recovery or {}).get("non_loan_total", 0)),
                          "shortfall": round((recovery or {}).get("shortfall", 0)),
                          "levers": [{"title": o["title"], "amount": round(o["amount"])}
                                     for o in (recovery or {}).get("options", [])]},
                "recovery": recovery, "target": None}

    # 2) Genuine spare + a loan to attack — the prepayment move.
    if actionable > 0 and first_step:
        headline = (f"Put your spare {_inr(actionable)} on {first_step['name']} — "
                    f"{_inr(first_step['monthly_payment'])}/mo clears it first.")
        return {"kind": "attack_debt", "headline": headline,
                "facts": {"actionable": actionable, "loan": first_step["name"],
                          "monthly_payment": round(first_step["monthly_payment"]),
                          "own_emi": round(first_step["own_emi"])},
                "recovery": None, "target": first_step}

    # 3) Within your means but under the savings target — nothing to prepay, tighten to free cash.
    if over_budget > 0:
        headline = (f"You’re inside your free cash but {_inr(over_budget)} under your savings plan — "
                    f"hold your budget to free {_inr(surplus)}/mo for debt.")
        return {"kind": "hold", "headline": headline,
                "facts": {"over_budget": over_budget, "surplus": surplus}, "recovery": None, "target": None}

    # 4) On track.
    return {"kind": "on_track",
            "headline": "You’re on track this cycle — no urgent money move.",
            "facts": {"surplus": surplus}, "recovery": None, "target": None}


_SYSTEM = (
    "You are Artha, a sharp household-finance coach. You are given the ONE money move already decided for "
    "this household, with EXACT numbers. Rephrase it as a single, natural, specific sentence a good CA "
    "would say — direct, warm, no fluff, no preamble, ≤ 30 words. Use ONLY the numbers provided; never "
    "invent, round differently, or add figures. Return just the sentence."
)


def narrate(move: dict) -> str:
    """One grounded sentence from the move's exact facts. Falls back to the deterministic headline when
    AI is unavailable or errors — the numbers are already correct either way."""
    import json

    from services import llm
    if move.get("kind") in ("on_track", "unknown") or not llm.available():
        return move["headline"]
    prompt = (f"Move: {move['kind']}\nFacts (numbers are FINAL): {json.dumps(move['facts'], ensure_ascii=False)}\n"
              f"Deterministic version: {move['headline']}\n\nWrite the one-sentence version.")
    out = llm.complete(prompt, system=_SYSTEM, max_tokens=120, timeout=12.0)
    return (out or move["headline"]).strip() or move["headline"]
