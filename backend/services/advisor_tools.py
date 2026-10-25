"""Deterministic Tools Layer — T3 in the Artha Advisor architecture contract.

Each function here is a household-scoped, DB-backed tool the advisor agent CALLS to fetch numbers,
replacing pre-stuffed prompt text with just-in-time ground truth. The model receives a tool result
and quotes from it; T7 (the number verifier) can then recompute the same value from the DB and catch
any hallucinated figure.

TENANCY RULE (HARD): `household_id` is injected by T4 (the tool-use loop) from the authenticated
HTTP request — it is NEVER declared in the Anthropic JSON schema (TOOLS_SPEC), so the LLM cannot
see, modify, or cross-tenant it. Every impl signature is:
    impl(db, household_id: int, **kwargs) -> dict
T4 calls: `impl(db, household_id, **block.input)` where `block.input` comes from the model.

FREE-CASH FIX (P0 bug): actually_free() is the ONLY tool that answers "how much can I spend?".
It returns pool − spent_this_period, never the gross leftover (which is the whole monthly budget,
not spare cash). The model is required to call actually_free() before any spend-headroom claim;
T7 fails the eval turn if a figure labelled 'free' or 'headroom' matches the gross pool instead.

CAPITAL-DECISION MATH (no duplication): the affordability() tool's big-ticket branch delegates
to api.afford._big_ticket_math — the SINGLE source of truth for HOME_LOAN_RATE, LTV, FOIR_CAP,
and the FOIR/LTV/liquidity-waterfall logic. Changing a constant in afford.py propagates to both
the /afford HTTP route and this tool automatically.

DEPENDENCY: no new pip packages. All imports are from the existing stack. T5 (knowledge.py) is
imported with a try/except so this module works even before T5 is merged.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

log = logging.getLogger("artha.advisor_tools")


def _current_month() -> str:
    """Today's calendar month as 'YYYY-MM'. Passed to compute_budget so the budget-backed
    tools always resolve the LIVE current period — never the latest-DATA month (the bug where
    the 'month' lens read June because June was the newest statement, not because it's today)."""
    return date.today().strftime("%Y-%m")


# ---------------------------------------------------------------------------
# Tool implementations
# Each impl:  (db, household_id, **kwargs) -> dict with a "summary" key.
# Local imports inside functions avoid import-time cycles (existing repo pattern).
# ---------------------------------------------------------------------------

def _impl_actually_free(db, household_id: int, *, lens: str, **_) -> dict:
    """THE FIX for the ₹150k free-cash bug.

    pool  = income − EMIs − fixed − saving − sinking − insurance  (the whole variable-spend budget)
    spent = variable spend already charged this cycle/month
    free  = pool − spent  ← the ONLY honest 'can I spend more?' figure.

    Never return `pool` as the free amount. Even when pool looks large (e.g. ₹1.5L), most of it is
    already consumed by groceries, dining, fuel. The tool always subtracts what's gone.
    """
    from api.budget import compute_budget
    from api.cashflow import compute_cashflow
    from services.money_format import household_symbol

    cash = compute_cashflow(db, household_id)
    b = compute_budget(month=_current_month(), mode=lens, db=db, household_id=household_id)
    c = household_symbol(db, household_id)
    pool = cash["leftover"]
    spent = b["net_spent_inr"]
    free = round(pool - spent, 2)

    summary = f"Pool {c}{pool:,.0f} − already-spent {c}{spent:,.0f} = {c}{free:,.0f} actually free"
    if free < 0:
        summary += " (already OVER the pool)"

    return {
        "lens": lens,
        "pool_inr": round(pool, 2),
        "spent_this_period_inr": round(spent, 2),
        "actually_free_inr": free,
        "over_budget": free < 0,
        "period_start": b.get("period_start"),
        "period_end": b.get("period_end"),
        "summary": summary,
    }


def _impl_budget_status(db, household_id: int, *, lens: str, **_) -> dict:
    """Full budget vs actual for the requested period lens, per category."""
    from api.budget import compute_budget
    from services.money_format import household_symbol

    b = compute_budget(month=_current_month(), mode=lens, db=db, household_id=household_id)
    total = round(sum(i["limit_inr"] for i in b["items"]), 2)
    net = b["net_spent_inr"]
    over_by = round(net - total, 2) if net > total else 0.0
    days_left = b["cycle"]["days_left"]

    items = [
        {
            "category": i["category"],
            "spent_inr": i["spent_inr"],
            "limit_inr": i["limit_inr"],
            "status": i.get("status", "ok"),
        }
        for i in b["items"]
    ]

    c = household_symbol(db, household_id)
    if net > total:
        summary = (
            f"Spent {c}{net:,.0f} vs budget {c}{total:,.0f} — "
            f"over by {c}{over_by:,.0f}; {days_left} days left"
        )
    else:
        remaining = round(total - net, 2)
        summary = (
            f"Spent {c}{net:,.0f} of {c}{total:,.0f} budget; "
            f"{c}{remaining:,.0f} unspent; {days_left} days left"
        )

    return {
        "lens": lens,
        "net_spent_inr": round(net, 2),
        "total_budget_inr": total,
        "over_by_inr": over_by,
        "days_left": days_left,
        "period_start": b.get("period_start"),
        "period_end": b.get("period_end"),
        "items": items,
        "summary": summary,
    }


def _impl_spend_by_category(db, household_id: int, *, lens: str, **_) -> dict:
    """Spend by category for the requested lens, sorted descending by amount spent."""
    from api.budget import compute_budget
    from services.money_format import household_symbol

    b = compute_budget(month=_current_month(), mode=lens, db=db, household_id=household_id)
    categories = sorted(
        [
            {
                "category": i["category"],
                "spent_inr": i["spent_inr"],
                "limit_inr": i["limit_inr"],
            }
            for i in b["items"]
            if i["spent_inr"] > 0
        ],
        key=lambda x: -x["spent_inr"],
    )

    if categories:
        sym = household_symbol(db, household_id)
        top = ", ".join(f'{c["category"]} {sym}{c["spent_inr"]:,.0f}' for c in categories[:3])
        summary = f"Top: {top}"
    else:
        summary = "No variable spend recorded for this period"

    return {
        "lens": lens,
        "categories": categories,
        "summary": summary,
    }


def _impl_emi_quote(db, household_id: int, *, principal: float, annual_pct: float,
                    months: int, **_) -> dict:
    """Deterministic reducing-balance EMI — the ONLY tool for loan arithmetic.

    The reckoner-discipline sanity check: total_payment_inr must exceed principal (else the
    tenure/rate are nonsensical). The model must never compute EMI itself; it calls this tool
    and quotes emi_inr directly.
    """
    from services.advisor import _emi  # the single source of truth for EMI math
    from services.money_format import household_symbol

    c = household_symbol(db, household_id)
    emi = _emi(float(principal), float(annual_pct), int(months))
    total_payment = round(emi * months, 2)
    total_interest = round(total_payment - principal, 2)

    # Guard: total payments must exceed principal (catches nonsensical inputs like months=0 or
    # an EMI that somehow doesn't cover interest). Strict inequality; zero-rate loans are fine
    # since emi*months == principal there (no interest).
    if total_payment < principal - 1:  # 1-unit tolerance for floating point
        raise ValueError(
            f"EMI sanity check failed: EMI×months {c}{total_payment:,.2f} < principal {c}{principal:,.2f}. "
            "Verify rate and tenure. Possible confusion: per-100k EMI vs full-loan EMI?"
        )

    emi_r = round(emi)
    summary = (
        f"{c}{principal:,.0f} at {annual_pct}% over {months}m "
        f"= {c}{emi_r:,.0f}/mo "
        f"(total {c}{total_payment:,.0f}, interest {c}{total_interest:,.0f})"
    )
    return {
        "principal_inr": principal,
        "annual_pct": annual_pct,
        "months": months,
        "emi_inr": emi_r,
        "total_payment_inr": total_payment,
        "total_interest_inr": total_interest,
        "summary": summary,
    }


def _impl_affordability(db, household_id: int, *, amount: float, lens: str, **_) -> dict:
    """Spend-vs-capital decision.

    Threshold (from afford.py): if amount >= max(₹2L, 3 × monthly income) it is a
    CAPITAL decision (house, car, large purchase) — uses the shared FOIR/LTV/liquidity
    waterfall from afford._big_ticket_math (the single source of truth for HOME_LOAN_RATE,
    LTV=0.80, FOIR_CAP=0.50, HOME_LOAN_YEARS=20 and the waterfall logic).
    Otherwise it is an everyday SPEND decision checked against actually_free.

    SCOPE vs THE /afford HTTP ENDPOINT:
      The /afford route binds on the MORE RESTRICTIVE of the overall variable-spend pool
      AND the chosen per-category budget limit (when a category is supplied). This tool
      has no category parameter — it checks the OVERALL pool only. Consequently:
        • /afford with category='Dining' on an exhausted dining budget → verdict='hold'
        • this tool on the same question → may return 'go' or 'tight'
      This is an intentional tool-layer simplification. Do NOT call this tool for a
      per-category check; instead call budget_status and inspect that category's
      spent_inr vs limit_inr.

    Capital-branch verdict (mirrors afford.py — same _big_ticket_math, same constants):
      hold  → shortfall exists AND FOIR exceeded  (can't fund the down payment OR afford the EMI)
      tight → fundable OR emi_ok (but not both)
      go    → fundable AND emi_ok

    Does NOT call the LLM. All numbers come from deterministic engines.
    """
    from api.cashflow import compute_cashflow
    from services.money_format import household_symbol

    cash = compute_cashflow(db, household_id)
    c = household_symbol(db, household_id)
    income = cash.get("income", 0) or 1

    if amount >= max(200_000, 3.0 * income):
        # ── Capital / big-ticket branch ─────────────────────────────────────────────
        # Delegate to the shared deterministic helper in afford.py — one source of truth
        # for all FOIR/LTV/liquidity-waterfall constants and logic. Changing a constant in
        # afford.py propagates here automatically; no manual sync required.
        from api.afford import _big_ticket_math
        from api.debt import compute_debt
        from api.networth import compute_assets

        existing_emi = compute_debt(db, household_id).get("total_emi", 0) or 0
        by_type      = compute_assets(db, household_id).get("by_type", {})

        m = _big_ticket_math(amount, cash, existing_emi, by_type)

        summary = (
            f"Capital decision ({c}{amount:,.0f}): "
            f"max loan {c}{m['max_loan']:,.0f}, down {c}{m['down_payment']:,.0f}, "
            f"new EMI {c}{m['new_emi']:,.0f} → FOIR {m['foir_pct']}% — {m['verdict'].upper()}"
        )
        if m["shortfall"] > 0:
            summary += f"; shortfall {c}{m['shortfall']:,.0f}"

        return {
            "amount_inr":      amount,
            "decision":        "capital",
            "verdict":         m["verdict"],
            "max_loan_inr":    m["max_loan"],
            "down_payment_inr": m["down_payment"],
            "funding":         m["funding"],
            "shortfall_inr":   m["shortfall"],
            "new_emi_inr":     m["new_emi"],
            "foir_pct":        m["foir_pct"],
            "summary":         summary,
        }

    # ── Everyday spend branch ────────────────────────────────────────────────────
    af = _impl_actually_free(db, household_id, lens=lens)
    free = af["actually_free_inr"]
    pool = af["pool_inr"]
    after = round(free - amount, 2)

    tight_band = 0.15 * max(pool, 1)
    if after < 0:
        verdict = "hold"
    elif after < tight_band:
        verdict = "tight"
    else:
        verdict = "go"

    summary = (
        f"{c}{amount:,.0f} vs actually-free {c}{free:,.0f} → "
        f"{c}{after:,.0f} remaining — {verdict.upper()}"
    )

    return {
        "amount_inr": amount,
        "decision": "spend",
        "verdict": verdict,
        "actually_free_inr": free,
        "remaining_after_inr": after,
        "summary": summary,
    }


def _impl_debt_snapshot(db, household_id: int, **_) -> dict:
    """All active loans sorted by highest rate first (avalanche order), plus FOIR room."""
    from api.cashflow import compute_cashflow
    from api.debt import compute_debt
    from services.money_format import household_symbol

    d = compute_debt(db, household_id)
    cash = compute_cashflow(db, household_id)
    sym = household_symbol(db, household_id)
    income = cash.get("income", 0) or 1
    existing_emi = cash.get("emi", 0) or 0
    foir_room = round(max(0.0, 0.50 * income - existing_emi), 2)

    # Order by EFFECTIVE rate, not headline. A deductible 9.5% education loan costs less than a
    # 9.1% gold loan that gets no relief, so ranking on the headline points at the cheapest debt
    # first. On the new regime nothing is deductible and this collapses back to the nominal order.
    from services.tax import payoff_order
    from services.tax_context import household_tax_context

    raw = [
        {
            "name": loan["name"],
            "balance_inr": loan["balance_inr"],
            "rate_pct": loan["rate_pct"],
            "emi_inr": loan["emi_inr"],
            "category": loan.get("category"),
            "holder": loan.get("holder"),
        }
        for loan in d["loans"]
        if not loan["closed"] and loan["balance_inr"] > 0
    ]
    regime, income_by_holder, let_out = household_tax_context(db, household_id)
    loans = payoff_order(raw, income_by_holder, regime, let_out_holders=let_out)

    total_balance = round(sum(l["balance_inr"] for l in loans), 2)
    total_emi = round(sum(l["emi_inr"] for l in loans), 2)

    if loans:
        top = f"{loans[0]['name']} @ {loans[0]['rate_pct']}%"
        summary = (
            f"{sym}{total_balance:,.0f} across {len(loans)} loan(s), "
            f"{sym}{total_emi:,.0f}/mo EMIs; "
            f"highest rate: {top}; "
            f"FOIR room {sym}{foir_room:,.0f}/mo"
        )
    else:
        summary = f"No active loans; FOIR room {sym}{foir_room:,.0f}/mo"

    return {
        "loans": loans,
        "total_balance_inr": total_balance,
        "total_emi_inr": total_emi,
        "foir_room_inr": foir_room,
        "summary": summary,
    }


def _impl_networth_snapshot(db, household_id: int, **_) -> dict:
    """Net assets by type and total. Use for liquidity and net-worth questions."""
    from api.networth import compute_assets
    from services.money_format import household_symbol

    nw = compute_assets(db, household_id)
    sym = household_symbol(db, household_id)
    total = nw["total_inr"]
    by_type = nw["by_type"]

    parts = ", ".join(
        f"{k} {sym}{v:,.0f}"
        for k, v in sorted(by_type.items(), key=lambda kv: -kv[1])
        if v > 0
    )
    summary = f"Net assets {sym}{total:,.0f}: {parts}" if parts else f"Net assets {sym}{total:,.0f}"

    return {
        "by_type": by_type,
        "total_inr": total,
        "summary": summary,
    }


def _impl_cashflow_snapshot(db, household_id: int, **_) -> dict:
    """Monthly cash-flow breakdown. IMPORTANT: leftover_inr is the POOL, not free cash.
    Call actually_free() before any 'can I spend more' claim — don't use leftover_inr for that.
    """
    from api.cashflow import compute_cashflow
    from services.money_format import household_symbol

    c = compute_cashflow(db, household_id)
    sym = household_symbol(db, household_id)
    income = c["income"]
    emi = c["emi"]
    fixed = c["fixed"]
    saving = c["saving"]
    sinking = c["sinking"]
    insurance = c["insurance"]
    leftover = c["leftover"]

    # "pool" in the summary, never "free" — the model must not read leftover as spare cash.
    summary = (
        f"Income {sym}{income:,.0f} → "
        f"EMIs {sym}{emi:,.0f}, "
        f"fixed {sym}{fixed:,.0f}, "
        f"save {sym}{saving:,.0f}, "
        f"insure {sym}{insurance:,.0f} "
        f"→ pool {sym}{leftover:,.0f}"
    )

    return {
        "income_inr": round(income, 2),
        "emi_inr": round(emi, 2),
        "fixed_inr": round(fixed, 2),
        "saving_inr": round(saving, 2),
        "sinking_inr": round(sinking, 2),
        "insurance_inr": round(insurance, 2),
        "leftover_inr": round(leftover, 2),
        "summary": summary,
    }


def _impl_ground(db, household_id: int, *, query: str, k: int = 4, **_) -> dict:
    """Retrieve financial definitions and rules from the Artha knowledge base (T5).

    Falls back gracefully if T5 (knowledge.py) is not yet merged — returns an empty facts list so
    the model can still proceed with the other tools.
    """
    try:
        from services.knowledge import ground  # T5 — provided by EM·Reasoning&Tools
        return ground(query, k=k)
    except ImportError:
        log.warning("knowledge.py (T5) not yet available — ground tool returning empty facts")
        return {
            "facts": [],
            "summary": "Knowledge base not yet available; rely on tool outputs for all numbers.",
        }
    except Exception as exc:  # noqa: BLE001 — knowledge retrieval must not crash the advisor
        log.warning("ground tool failed: %s", exc)
        return {"facts": [], "summary": f"Knowledge retrieval error: {exc}"}


# ---------------------------------------------------------------------------
# TOOLS_SPEC — Anthropic tool schemas for T4's tool-use loop.
# HARD RULE: household_id is NEVER declared here. It is injected server-side by T4.
# ---------------------------------------------------------------------------

TOOLS_SPEC: list[dict] = [
    {
        "name": "actually_free",
        "description": (
            "THE authoritative answer to 'how much can I spend right now?'. "
            "Returns pool (monthly variable-spend budget) MINUS already-spent-this-period. "
            "ALWAYS call this before any 'can I spend' or 'how much headroom' claim. "
            "NEVER present the gross pool (leftover) as free cash — it is the whole budget, "
            "most of which is already consumed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "lens": {
                    "type": "string",
                    "enum": ["cycle", "month"],
                    "description": (
                        "'cycle' = per-card billing cycle (default, canonical). "
                        "'month' = calendar month. Use the user's active lens."
                    ),
                },
            },
            "required": ["lens"],
        },
    },
    {
        "name": "budget_status",
        "description": (
            "Returns budget vs actual spend for the requested lens with per-category "
            "breakdown, over/under amount, and days left in the period. "
            "Use for 'are we on track?' or 'how much have we spent?' questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "lens": {
                    "type": "string",
                    "enum": ["cycle", "month"],
                    "description": "Period lens: 'cycle' or 'month'.",
                },
            },
            "required": ["lens"],
        },
    },
    {
        "name": "spend_by_category",
        "description": (
            "Returns spend by category for the requested lens, sorted descending by amount. "
            "Use for 'where is the money going?' or 'which category is highest?' questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "lens": {
                    "type": "string",
                    "enum": ["cycle", "month"],
                    "description": "Period lens: 'cycle' or 'month'.",
                },
            },
            "required": ["lens"],
        },
    },
    {
        "name": "emi_quote",
        "description": (
            "Computes a deterministic reducing-balance EMI. "
            "ALWAYS use this for any loan/EMI calculation — never compute EMI manually. "
            "Sanity-checks that total payments exceed the principal. "
            "Returns emi_inr (monthly), total_payment_inr, total_interest_inr."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "principal": {
                    "type": "number",
                    "description": "Loan amount in INR (e.g. 800000 for ₹8L).",
                },
                "annual_pct": {
                    "type": "number",
                    "description": (
                        "Annual interest rate as a percentage "
                        "(e.g. 8.75 for 8.75% p.a., NOT 0.0875)."
                    ),
                },
                "months": {
                    "type": "integer",
                    "description": "Loan tenure in months (e.g. 60 for 5 years, 240 for 20 years).",
                },
            },
            "required": ["principal", "annual_pct", "months"],
        },
    },
    {
        "name": "affordability",
        "description": (
            "Checks whether the household can afford a specific purchase. "
            "For amounts >= max(₹2,00,000, 3x monthly income): capital-decision analysis using "
            "FOIR cap, LTV, and liquidity waterfall (house, car, large one-off). "
            "For everyday amounts: checks against actually_free (OVERALL pool only — "
            "not per category; for a category-specific check use budget_status instead). "
            "Returns verdict ('go' / 'tight' / 'hold') and the key numbers. "
            "Use for any 'can I afford / buy X?' question."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "amount": {
                    "type": "number",
                    "description": "Purchase price in INR.",
                },
                "lens": {
                    "type": "string",
                    "enum": ["cycle", "month"],
                    "description": (
                        "Period lens for the everyday-spend branch. "
                        "Use the user's active lens."
                    ),
                },
            },
            "required": ["amount", "lens"],
        },
    },
    {
        "name": "debt_snapshot",
        "description": (
            "Returns all active loans sorted by highest rate first (avalanche order), "
            "plus total balance, total monthly EMI, and FOIR room "
            "(how much additional EMI the household can absorb without exceeding 50% FOIR). "
            "Use for loan / prepay / restructuring questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "networth_snapshot",
        "description": (
            "Returns the household's net assets by type "
            "(cash, gold, stock/equity, property, etc.) and total value in INR. "
            "Use for net-worth, liquidity, or 'what can I liquidate?' questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "cashflow_snapshot",
        "description": (
            "Returns the monthly cash-flow breakdown: income, EMIs, fixed expenses, "
            "saving contributions, sinking fund set-asides, insurance, and leftover. "
            "IMPORTANT: leftover_inr is the variable-spend POOL, not free cash. "
            "Call actually_free() to get what is truly unspent this period."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "ground",
        "description": (
            "Retrieves financial DEFINITIONS and RULES from the Artha knowledge base. "
            "Use this to ground any definitional claim: what 'leftover' means, "
            "how actually_free is computed, FOIR 50% cap, cycle vs month lens, "
            "spend vs capital threshold, money-slice definitions. "
            "Do NOT invent or reason about definitions — look them up here."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "The definition or rule to look up "
                        "(e.g. 'what is leftover', 'FOIR cap', 'actually free formula', "
                        "'cycle vs month')."
                    ),
                },
                "k": {
                    "type": "integer",
                    "description": "Number of facts to retrieve (default 4, max 8).",
                    "default": 4,
                },
            },
            "required": ["query"],
        },
    },
]


# ---------------------------------------------------------------------------
# TOOL_IMPLS — name → callable(db, household_id, **kwargs) -> dict.
# T4 imports this dict and dispatches each tool_use block here.
# household_id is injected by T4 from the authenticated request — NOT from block.input.
# ---------------------------------------------------------------------------

TOOL_IMPLS: dict[str, Any] = {
    "actually_free":     _impl_actually_free,
    "budget_status":     _impl_budget_status,
    "spend_by_category": _impl_spend_by_category,
    "emi_quote":         _impl_emi_quote,
    "affordability":     _impl_affordability,
    "debt_snapshot":     _impl_debt_snapshot,
    "networth_snapshot": _impl_networth_snapshot,
    "cashflow_snapshot": _impl_cashflow_snapshot,
    "ground":            _impl_ground,
}
