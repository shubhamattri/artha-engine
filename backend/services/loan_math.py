"""Loan mathematics — pure functions. NOTHING here is hardcoded per-loan; every
output (EMI, balance, months left, interest) is computed from the loan's inputs:
original principal, annual rate, tenure, disbursement date, and any prepayments.

Standard reducing-balance amortisation:
    monthly rate  r = annual_rate / 12
    EMI           E = P·r·(1+r)^n / ((1+r)^n − 1)        (n = tenure months)
    balance after k paid:  B_k = P·(1+r)^k − E·((1+r)^k − 1)/r
    months left given balance B, EMI E:  n = ln(E / (E − B·r)) / ln(1+r)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date


def emi(principal: float, annual_rate_pct: float, tenure_months: int) -> float:
    """Equated Monthly Instalment for a reducing-balance loan."""
    if tenure_months <= 0:
        return 0.0
    r = (annual_rate_pct / 100.0) / 12.0
    if r == 0:
        return principal / tenure_months
    f = (1 + r) ** tenure_months
    return principal * r * f / (f - 1)


def balance_after(principal: float, annual_rate_pct: float, tenure_months: int, k: int) -> float:
    """Outstanding balance after k EMIs have been paid."""
    if k <= 0:
        return principal
    if k >= tenure_months:
        return 0.0
    r = (annual_rate_pct / 100.0) / 12.0
    e = emi(principal, annual_rate_pct, tenure_months)
    if r == 0:
        return max(0.0, principal - e * k)
    f = (1 + r) ** k
    return max(0.0, principal * f - e * (f - 1) / r)


def months_between(start: date, end: date) -> int:
    """Whole months elapsed from start to end (EMIs assumed paid monthly)."""
    m = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        m -= 1
    return max(0, m)


def months_remaining(balance: float, annual_rate_pct: float, emi_amount: float) -> int:
    """How many EMIs remain to clear `balance` at this EMI and rate.

    A small tolerance is subtracted so that EMI-rounding residuals (e.g. a loan
    that mathematically needs 1.00004 payments) count as the final payment, not
    a spurious extra month.
    """
    if balance <= 0 or emi_amount <= 0:
        return 0
    r = (annual_rate_pct / 100.0) / 12.0
    if r == 0:
        return max(1, math.ceil(balance / emi_amount - 1e-4))
    if emi_amount <= balance * r:
        return 999  # EMI doesn't even cover interest — never amortises
    n = math.log(emi_amount / (emi_amount - balance * r)) / math.log(1 + r)
    return max(1, math.ceil(n - 1e-4))


@dataclass
class LoanState:
    emi: float
    months_passed: int
    months_left: int
    current_balance: float
    principal_paid: float
    interest_paid: float
    total_interest_over_life: float
    next_due: date | None
    progress_pct: float  # how much principal repaid, 0–100


def compute_state(
    principal: float,
    annual_rate_pct: float,
    tenure_months: int,
    disbursement: date,
    today: date,
    extra_principal: float = 0.0,
    closed: bool = False,
    closed_on: date | None = None,
) -> LoanState:
    """Full derived state for a loan as of `today`. `extra_principal` = lump-sum
    prepayments already made (reduces balance, shortens tenure)."""
    e = emi(principal, annual_rate_pct, tenure_months)
    total_interest = max(0.0, e * tenure_months - principal)

    if closed:
        # A closed loan reported the FULL schedule: months_passed = tenure and interest_paid = the
        # whole life's interest. That describes a loan that ran to term. These were PREPAID, so it
        # credited interest that was specifically avoided — the entire point of prepaying. An ICICI
        # loan that ran 23 of 84 months showed 84 months and Rs 82,000 of interest against Rs 36,000
        # actually paid; across five closed loans it overstated interest by Rs 4 lakh, and the UI
        # printed the tenure as "N yrs ago".
        k_end = min(months_between(disbursement, closed_on or today), tenure_months)
        bal_at_close = balance_after(principal, annual_rate_pct, tenure_months, k_end)
        paid_interest = max(0.0, e * k_end - (principal - bal_at_close))
        # principal_paid stays the full principal: it IS fully repaid, the remainder as a lump sum.
        return LoanState(e, k_end, 0, 0.0, principal, round(paid_interest, 2),
                         total_interest, None, 100.0)

    k = min(months_between(disbursement, today), tenure_months)
    bal_scheduled = balance_after(principal, annual_rate_pct, tenure_months, k)
    bal = max(0.0, bal_scheduled - extra_principal)

    principal_paid = principal - bal
    interest_paid = max(0.0, e * k - (principal - bal_scheduled))

    # Months left = the contractual remainder for a normal loan (exact, no rounding artifact).
    # Recompute from the balance whenever it has moved off the schedule in EITHER direction: a
    # prepayment shortens it, a skipped EMI lengthens it. Testing `> 0` only saw the first, so a
    # missed EMI raised the balance and left the closure date untouched.
    if bal <= 0:
        left = 0
    elif extra_principal != 0:
        left = months_remaining(bal, annual_rate_pct, e)
    else:
        left = max(0, tenure_months - k)

    next_due = None
    if bal > 0:
        ny, nm = disbursement.year, disbursement.month + (k + 1)
        ny += (nm - 1) // 12
        nm = (nm - 1) % 12 + 1
        try:
            next_due = date(ny, nm, min(disbursement.day, 28))
        except ValueError:
            next_due = date(ny, nm, 28)

    progress = (principal_paid / principal * 100.0) if principal else 100.0
    return LoanState(e, k, left, bal, principal_paid, interest_paid, total_interest,
                     next_due, min(100.0, max(0.0, progress)))


def compute_flexi_state(
    principal: float,
    drawn: float,
    annual_rate_pct: float,
    tenure_months: int,
    moratorium_months: int,
    disbursement: date,
    today: date,
    interest_mode: str = "full",   # full | partial | none
) -> tuple[LoanState, dict]:
    """Education/flexi loan: during the moratorium you pay (simple) interest only on
    the DRAWN amount — not a full EMI. Unpaid interest (partial/none) capitalises into
    principal; the full amortising EMI begins after the moratorium.

    Returns (LoanState, extra) where extra has phase + post_moratorium_emi.
    """
    r = (annual_rate_pct / 100.0) / 12.0
    drawn = drawn or principal
    elapsed = months_between(disbursement, today)
    post_tenure = max(1, tenure_months - moratorium_months)
    monthly_interest = drawn * r

    if elapsed < moratorium_months:
        # ── In moratorium: interest-only on drawn ──
        if interest_mode == "none":
            bal = drawn * ((1 + r) ** elapsed)        # interest compounds onto principal
            current_pay, paid = 0.0, 0.0
        elif interest_mode == "partial":
            bal = drawn + monthly_interest * elapsed * 0.5
            current_pay, paid = monthly_interest * 0.5, monthly_interest * 0.5 * elapsed
        else:  # full — user services the interest, principal stays flat
            bal = drawn
            current_pay, paid = monthly_interest, monthly_interest * elapsed
        post_emi = emi(bal, annual_rate_pct, post_tenure)
        state = LoanState(
            emi=current_pay, months_passed=elapsed,
            months_left=tenure_months - elapsed, current_balance=round(bal, 2),
            principal_paid=0.0, interest_paid=round(paid, 2),
            total_interest_over_life=round(post_emi * post_tenure - drawn, 2),
            next_due=None, progress_pct=0.0,
        )
        return state, {"phase": "moratorium", "post_moratorium_emi": round(post_emi, 2),
                       "monthly_interest": round(monthly_interest, 2), "drawn": drawn}

    # ── Post-moratorium: amortise the (capitalised) base over the post tenure ──
    base = drawn
    if interest_mode == "none":
        base = drawn * ((1 + r) ** moratorium_months)
    elif interest_mode == "partial":
        base = drawn + monthly_interest * moratorium_months * 0.5
    k2 = min(elapsed - moratorium_months, post_tenure)
    e = emi(base, annual_rate_pct, post_tenure)
    bal = balance_after(base, annual_rate_pct, post_tenure, k2)
    state = LoanState(
        emi=e, months_passed=elapsed, months_left=max(0, post_tenure - k2),
        current_balance=round(bal, 2), principal_paid=round(base - bal, 2),
        interest_paid=round(max(0.0, e * k2 - (base - bal)), 2),
        total_interest_over_life=round(e * post_tenure - drawn, 2),
        next_due=None, progress_pct=round((base - bal) / base * 100.0 if base else 100.0, 1),
    )
    return state, {"phase": "repaying", "post_moratorium_emi": round(e, 2),
                   "monthly_interest": round(bal * r, 2), "drawn": drawn}


def _add_months(d: date, k: int) -> date:
    """d shifted k whole months, clamping the day to the target month length (31 Jan +1 → 28 Feb)."""
    idx = d.year * 12 + (d.month - 1) + k
    y, m = idx // 12, idx % 12 + 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(y, m, day)
        except ValueError:
            continue
    return date(y, m, 28)


def compute_gold_state(
    principal: float,
    annual_rate_pct: float,
    disbursement: date,
    today: date,
    payments: list[dict] | None = None,
    expected_monthly_payment: float = 0.0,
    closed: bool = False,
) -> tuple[LoanState, dict]:
    """A gold loan is a running-balance account, NOT a fixed-EMI loan. Simple interest accrues monthly on
    the outstanding principal; each payment (any amount, any date, several, or none) clears accrued
    interest first, then chips principal. Bullet by nature — the principal isn't scheduled down, it only
    drops when a payment exceeds the interest. `expected_monthly_payment` drives an open-ended payoff
    projection (months_left); it is NOT an amortising EMI. Returns (LoanState, extra)."""
    r = (annual_rate_pct / 100.0) / 12.0
    payments = payments or []

    if closed:
        return (LoanState(0.0, months_between(disbursement, today), 0, 0.0, principal, 0.0, 0.0, None,
                          100.0),
                {"phase": "closed", "monthly_interest": 0.0, "principal_outstanding": 0.0,
                 "interest_due": 0.0, "interest_paid": 0.0, "total_paid": 0.0})

    m = months_between(disbursement, today)
    # Chronological event stream: interest for each elapsed month is due from that month's START, plus
    # every payment on its date. Accrual sorts before a same-day payment (the month's interest is due
    # before you pay it), so a mid-month payment can clear that month's interest then chip principal.
    events: list[tuple[date, int, float]] = [(_add_months(disbursement, k), 0, 0.0)
                                             for k in range(0, m)]
    logged_months: set[tuple[int, int]] = set()
    for p in payments:
        d = p["date"]
        if isinstance(d, str):
            d = date.fromisoformat(d[:10])
        if disbursement <= d <= today:
            events.append((d, 1, float(p.get("amount") or 0.0)))
            logged_months.add((d.year, d.month))

    # The expected payment is money that LEAVES THE ACCOUNT every month. Requiring it to be logged
    # before it counts made the balance climb by the full interest for every unlogged month, which is
    # simply wrong: the household is paying, it just is not typing it in. So assume the expected
    # amount was paid in any elapsed month with no logged payment. A logged payment always wins for
    # its own month — that is how a part payment, a skipped month (log 0), or an irregular amount is
    # expressed.
    if expected_monthly_payment > 0:
        for k in range(0, m):
            due = _add_months(disbursement, k)
            if (due.year, due.month) not in logged_months:
                events.append((due, 1, float(expected_monthly_payment)))

    events.sort(key=lambda e: (e[0], e[1]))

    principal_out = float(principal)
    interest_due = interest_paid = principal_paid = 0.0
    for _d, kind, amt in events:
        if kind == 0:                                  # monthly interest accrual on current principal
            interest_due += principal_out * r
        else:                                          # a payment: interest first, remainder → principal
            to_int = min(amt, interest_due)
            interest_due -= to_int
            interest_paid += to_int
            to_prin = min(amt - to_int, principal_out)  # never overpay principal below zero
            principal_out -= to_prin
            principal_paid += to_prin

    monthly_interest = principal_out * r
    # Payoff horizon: paid a FIXED expected amount each month, a gold loan AMORTISES — interest shrinks as
    # principal drops, so the honest projection is the amortisation formula, NOT principal/(payment−interest)
    # (which wrongly assumes the tiny first-month chip stays constant → a 2000+ month over-estimate). If the
    # payment doesn't even cover the current interest, principal never falls → "never" sentinel.
    if principal_out <= 0:
        months_left = 0
    elif expected_monthly_payment > monthly_interest:
        months_left = months_remaining(principal_out, annual_rate_pct, expected_monthly_payment)
    else:
        months_left = 999

    progress = (principal_paid / principal * 100.0) if principal else 100.0
    state = LoanState(
        emi=round(monthly_interest, 2), months_passed=m, months_left=months_left,
        current_balance=round(principal_out + interest_due, 2),
        principal_paid=round(principal_paid, 2), interest_paid=round(interest_paid, 2),
        total_interest_over_life=round(interest_paid + interest_due, 2),
        next_due=_add_months(disbursement, m) if principal_out > 0 else None,
        progress_pct=min(100.0, max(0.0, progress)),
    )
    extra = {"phase": "active", "monthly_interest": round(monthly_interest, 2),
             "principal_outstanding": round(principal_out, 2), "interest_due": round(interest_due, 2),
             "interest_paid": round(interest_paid, 2),
             "total_paid": round(interest_paid + principal_paid, 2)}
    return state, extra


def amortization_schedule(
    principal: float, annual_rate_pct: float, tenure_months: int, max_rows: int = 360
) -> list[dict]:
    """Per-EMI breakdown of principal vs interest and running balance, from origination."""
    r = (annual_rate_pct / 100.0) / 12.0
    e = emi(principal, annual_rate_pct, tenure_months)
    bal = principal
    rows = []
    for i in range(1, min(tenure_months, max_rows) + 1):
        interest = bal * r
        princ = e - interest
        bal = max(0.0, bal - princ)
        rows.append({
            "n": i,
            "emi": round(e, 2),
            "principal": round(princ, 2),
            "interest": round(interest, 2),
            "balance": round(bal, 2),
        })
    return rows


def forward_schedule(
    current_balance: float, annual_rate_pct: float, monthly_payment: float,
    start: date, max_rows: int = 360, steps: list[tuple[int, float]] | None = None,
) -> list[dict]:
    """The schedule that actually helps you plan a foreclosure: it starts from TODAY'S balance, not
    the original principal, and carries a real calendar month against every row.

    A schedule run from origination answers "what did I owe in month 7 of a loan I took in 2024",
    which is history. The question being asked is "if I save up, which month can I clear this, and
    how much will it cost me that month" — so every row carries `closing_cost`, what it takes to
    walk in and close the account after that month's EMI.

    Car and personal loans usually forbid part-prepayment but allow full foreclosure, which is
    exactly why the month-by-month closing cost is the number worth seeing.

    `steps` handles any loan whose payment CHANGES over time, as [(from_month, amount), ...] with
    from_month 1-based. A flexi/education loan in moratorium pays interest only and then jumps to the
    full EMI; without this it looks like a loan that never clears, because the opening payment equals
    the interest. Standard and gold loans just pass a single flat payment. Nothing here knows what
    kind of loan it is — the caller supplies the steps, so a future product with a teaser rate or a
    step-up EMI works with no change here."""
    r = (annual_rate_pct / 100.0) / 12.0
    bal = float(current_balance)
    rows: list[dict] = []
    plan = sorted(steps or [(1, monthly_payment)])
    if bal <= 0 or not plan or all(a <= 0 for _, a in plan):
        return rows

    def _pay_at(month: int) -> float:
        amt = 0.0
        for frm, a in plan:
            if month >= frm:
                amt = a
        return amt

    interest_running = 0.0
    for i in range(1, max_rows + 1):
        interest = bal * r
        pay = _pay_at(i)
        if pay <= interest:
            # Interest-only for now. Principal does not move, but the clock does — a moratorium ends
            # and the payment steps up. Only give up once no later step can ever cover the interest.
            if all(a <= interest for frm, a in plan if frm > i) and _pay_at(max_rows) <= interest:
                break
            interest_running += max(0.0, interest - pay)   # unpaid interest accrues
            due = _add_months(start, i)
            rows.append({
                "n": i, "month": due.isoformat(), "label": due.strftime("%b %Y"),
                "emi": round(pay, 2), "principal": 0.0, "interest": round(interest, 2),
                "balance": round(bal, 2), "closing_cost": round(bal, 2),
                "interest_paid_so_far": round(interest_running, 2),
                "interest_saved_if_closed": None,
            })
            continue
        princ = min(pay - interest, bal)
        bal = max(0.0, bal - princ)
        interest_running += interest
        due = _add_months(start, i)
        rows.append({
            "n": i,
            "month": due.isoformat(),
            "label": due.strftime("%b %Y"),
            "emi": round(pay, 2),
            "principal": round(princ, 2),
            "interest": round(interest, 2),
            "balance": round(bal, 2),
            # What closing the account costs right after this month's payment. Foreclosure charges
            # are lender-specific and are NOT included — this is the outstanding principal only.
            "closing_cost": round(bal, 2),
            "interest_paid_so_far": round(interest_running, 2),
            # Interest you never pay if you close here instead of running to term.
            "interest_saved_if_closed": None,   # filled below, needs the full run first
        })
        if bal <= 0:
            break
    total_interest = rows[-1]["interest_paid_so_far"] if rows else 0.0
    truncated = bool(rows) and rows[-1]["balance"] > 0      # still owing when the cap was hit
    for row in rows:
        row["interest_saved_if_closed"] = round(total_interest - row["interest_paid_so_far"], 2)
        # Honest about the cap: on a truncated run "saved" is measured against the last row we
        # computed, not against term, so it understates. Say so rather than imply a full picture.
        row["truncated"] = truncated
    return rows
