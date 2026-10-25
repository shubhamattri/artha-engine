"""compute_month_state — the canonical "this household, this month" truth (review #1).

Every tab (Cockpit, Commitments, Debt, Budget) should read the household's monthly picture from HERE, so
cashflow, commitments (paid/missing), debt spare, due items and safe-to-spend never diverge tab-to-tab.
It composes the existing engines ONCE and layers the cross-cutting derived facts on top:

  • cashflow            — income / EMIs / fixed / saving / sinking / insurance / leftover
  • reconcile           — commitments matched to real debits: paid (ACTUAL) / missing / extra
  • remaining_dues      — planned commitments NOT yet paid this month
  • coming_up           — the still-unpaid commitments (status-driven, NOT amount-guessing — #5)
  • debt                — surplus context + the one money move (money_moves engine)

Loan EMIs everywhere come through services.loan_obligation, so a flexi/moratorium loan reads the same
number here as on the Debt tab. Read-only.
"""

from __future__ import annotations

from datetime import date

# commitment "kind" → the Coming-up display group.
_GROUPS = [
    ("Loan EMIs", {"EMI"}),
    ("SIPs & savings", {"SIP", "Saving"}),
    ("Rent & household", {"Fixed"}),
    ("Insurance", {"Insurance"}),
]


def coming_up_from_reconcile(rec: dict) -> list[dict]:
    """Still-due THIS month = commitments the reconciler hasn't matched to a real debit yet. Status-
    driven: the moment a matching debit lands, that item drops off 'coming up' on its own — no guessing a
    due day from a similar-looking amount (which broke when amounts repeat)."""
    missing = rec.get("missing", [])
    out: list[dict] = []
    for label, kinds in _GROUPS:
        items = [{"name": c["name"], "amount": c["amount"], "day": None}
                 for c in missing if c.get("kind") in kinds]
        if items:
            out.append({"label": label, "amount": round(sum(i["amount"] for i in items), 2),
                        "count": len(items), "items": sorted(items, key=lambda x: -x["amount"])})
    return out


def compute_month_state(db, household_id: int | None, month: str | None = None, mode: str = "cycle") -> dict:
    today = date.today()
    month = month or today.strftime("%Y-%m")

    from api.cashflow import compute_cashflow
    from api.debt import compute_debt
    from api.sinking import compute_sinking
    from services.commitments import reconcile

    cash = compute_cashflow(db, household_id)
    debt = compute_debt(db, household_id)
    sinking = compute_sinking(db, household_id)
    rec = reconcile(db, household_id, month)
    strategies = debt.get("strategies", {})

    return {
        "month": month, "mode": mode,
        "cashflow": cash,
        "sinking": sinking,
        "reconcile": rec,
        "remaining_dues": round(rec.get("missing_total", 0.0), 2),   # planned commitments not yet paid
        "coming_up": coming_up_from_reconcile(rec),
        "debt": {
            "total_balance": debt.get("total_balance"),
            "total_emi": debt.get("total_emi"),
            "monthly_interest": debt.get("monthly_interest"),
            "surplus_context": strategies.get("surplus_context"),
            "move": strategies.get("move"),
        },
    }
