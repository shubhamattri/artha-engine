"""ONE shared truth for a loan's current obligation — its live state (balance, EMI, months-left) and the
monthly EMI it actually costs THIS month, WITH flexi/moratorium handling.

Why this exists: Debt, Cashflow, and Commitments each computed a loan's EMI a DIFFERENT way — Debt via
flexi-aware `compute_flexi_state`, Cashflow via plain `compute_state`, Commitments via the raw
amortization `loan_math.emi()` formula. For a flexi/moratorium loan (e.g. ICICI Education, which pays
interest-only during its moratorium) those three disagree, so the SAME loan showed three EMIs across
tabs. Every tab must call this instead.
"""

from __future__ import annotations

from datetime import date

from services import loan_math


def is_flexi(loan) -> bool:
    return (getattr(loan, "loan_type", None) or "standard") == "flexi"


def is_gold(loan) -> bool:
    return (getattr(loan, "loan_type", None) or "standard") == "gold"


def _gold_payments(loan) -> list[dict]:
    """The loan's payment ledger as plain dicts for loan_math (any amount, any date, many or none)."""
    return [{"date": p.date, "amount": p.amount} for p in (getattr(loan, "payments", None) or [])]


def flexi_terms(loan) -> dict:
    return getattr(loan, "flexi_terms", None) or {}


def drawn(loan) -> float:
    """Amount actually disbursed — sum of tranches if any, else the single disbursed_amount, else the
    full sanctioned principal."""
    ft = flexi_terms(loan)
    tr = ft.get("disbursements") or []
    if tr:
        return sum(float(t.get("amount", 0)) for t in tr)
    return ft.get("disbursed_amount") or loan.original_principal


def state_of(loan, today: date | None = None):
    """(LoanState, extra_dict). Flexi/moratorium loans use the flexi model (interest-only during the
    moratorium, full EMI after); standard loans use straight amortization. This is the SINGLE place that
    decision is made."""
    today = today or date.today()
    if is_gold(loan):
        return loan_math.compute_gold_state(
            loan.original_principal, loan.annual_rate_pct, loan.disbursement_date, today,
            _gold_payments(loan), getattr(loan, "expected_monthly_payment", None) or 0.0, loan.closed,
        )
    if is_flexi(loan):
        ft = flexi_terms(loan)
        return loan_math.compute_flexi_state(
            loan.original_principal, drawn(loan), loan.annual_rate_pct, loan.tenure_months,
            ft.get("moratorium_months") or 0, loan.disbursement_date, today,
            ft.get("interest_mode") or "full",
        )
    # Prepayments logged against a standard/flexi loan are EXTRA, on top of the EMI the amortisation
    # already assumes. Only gold loans were reading the ledger, so a payment recorded on any other
    # loan was written and then ignored: the balance never moved and the payoff plan never shifted.
    # Approximation: an old prepayment is credited at its face value rather than the interest it has
    # since avoided, so this understates rather than flatters.
    ledger = getattr(loan, "payments", None) or []
    paid_extra = sum(p.amount or 0.0 for p in ledger if (p.kind or "extra") != "skipped_emi")
    # A skipped EMI is the mirror of a prepayment: the amortisation already assumed it was paid, so
    # the real balance is HIGHER by what never left the account. Credited at face value, like a
    # prepayment, so it understates the damage rather than overstating it.
    skipped = sum(p.amount or 0.0 for p in ledger if (p.kind or "extra") == "skipped_emi")
    extra = (loan.extra_principal or 0.0) + paid_extra - skipped
    st = loan_math.compute_state(
        loan.original_principal, loan.annual_rate_pct, loan.tenure_months,
        loan.disbursement_date, today, extra, loan.closed,
        getattr(loan, "closed_on", None),
    )
    return st, {}


def monthly_emi(loan, today: date | None = None) -> float:
    """The loan's CURRENT monthly EMI obligation — an explicit override if set, else the (flexi-aware)
    state EMI. THE number Debt/Cashflow/Commitments must all agree on. A gold loan has no amortising EMI:
    its monthly outflow is the nominal `expected_monthly_payment` the household chooses to pay (falling
    back to the interest-only figure), NOT a formula EMI on the principal."""
    if loan.emi_override:
        return loan.emi_override
    if is_gold(loan):
        st, _ = state_of(loan, today)
        return getattr(loan, "expected_monthly_payment", None) or st.emi   # nominal outflow, else interest
    st, _ = state_of(loan, today)
    return st.emi
