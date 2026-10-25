"""Will this lender take your money early, and what does it cost?

A payoff plan that part-pays a loan the lender refuses is promising a date that cannot happen. But
this is not a per-loan fact to be typed in by hand — for most Indian retail borrowers it is decided
by REGULATION, and the deciding input is the RATE TYPE, not the loan category. That surprises people:
a floating car loan is freely prepayable while a fixed home loan may not be.

RBI (Pre-payment Charges on Loans) Directions, 2025, effective 1 Jan 2026:
  • Floating-rate term loans to INDIVIDUALS for non-business purposes carry NO pre-payment or
    foreclosure charge, in part or in full, with NO minimum lock-in, whatever the source of funds.
  • From 1 Jan 2026 the same extends to floating-rate business loans to individuals and MSEs, for
    facilities sanctioned or renewed on or after that date.
  • FIXED-rate loans are left to the lender, who must disclose the charge in the sanction letter,
    loan agreement and Key Facts Statement. An undisclosed charge cannot be collected later.

So the default is derived, and only a fixed-rate loan needs its sanction letter read. `Loan.prepayment`
and `Loan.prepayment_charge_pct` stay as explicit overrides for when the paperwork disagrees with the
derived default, which is what governs — this module supplies the default, never the last word.
"""

from __future__ import annotations

# What the lender will accept.
PART = "part"                        # any amount, any time
FORECLOSE_ONLY = "foreclose_only"    # nothing until it clears the whole balance
NONE = "none"                        # refuses early money entirely

RBI_FLOATING_RULE = "RBI Pre-payment Charges on Loans Directions 2025 (floating, individual)"


def derive(loan) -> tuple[str, float, str]:
    """(rule, charge_pct, why) for a loan whose terms were not explicitly overridden.

    Returns the charge as a percentage of the outstanding principal, 0.0 when the law forbids one.
    """
    rate_type = (getattr(loan, "rate_type", None) or "fixed").lower()
    loan_type = (getattr(loan, "loan_type", None) or "standard").lower()
    category = (getattr(loan, "category", None) or "").lower()

    # A gold loan is a running-balance account, not a term loan. Paying in is how it works: every
    # payment clears accrued interest first and then chips principal. Nothing to permit.
    if loan_type == "gold":
        return PART, 0.0, "gold loans are running accounts — any payment, any time, by design"

    # The regulation, and the reason a floating car loan is freely prepayable.
    if rate_type == "floating":
        return PART, 0.0, RBI_FLOATING_RULE

    # Fixed rate: the lender sets the terms. Vehicle and personal loans from NBFCs typically refuse
    # part payment and charge to foreclose, so that is the safe default — it under-promises rather
    # than planning around money a lender may hand straight back. The real number is in the sanction
    # letter and belongs in the override.
    if category in {"car", "vehicle", "personal", "credit card"}:
        return (FORECLOSE_ONLY, 3.0,
                "fixed-rate vehicle/personal loan — lender's terms apply; charge is a placeholder "
                "until the sanction letter is read")

    # Fixed-rate home and education loans usually permit part payment, often without charge, but the
    # law does not compel it.
    return PART, 0.0, "fixed rate — lender's terms apply; verify against the sanction letter"


def effective(loan) -> tuple[str, float, str]:
    """What actually governs: an explicit override if present, otherwise the derived default."""
    override = getattr(loan, "prepayment", None)
    if override:
        return (override,
                getattr(loan, "prepayment_charge_pct", None) or 0.0,
                "set explicitly on the loan")
    return derive(loan)


# What idle cash earns while it waits to become a foreclosure payment. Arbitrage-fund territory:
# equity-taxed, T+1, the honest place to park money with a known near-term use.
PARKING_RATE_PCT = 6.5


def should_accumulate(foreclose_rate_pct: float, next_best_rate_pct: float | None,
                      parking_rate_pct: float = PARKING_RATE_PCT) -> tuple[bool, str]:
    """Is it worth SAVING UP to foreclose a loan that refuses part payment, rather than sending the
    money at a loan that will take it?

    Redirected cash earns the rate of whatever loan it kills, immediately and with certainty. Parked
    cash earns the parking rate while it waits. So accumulating costs (next_best - parking) a year on
    a growing pile, to save (foreclose - next_best). It only wins when the stubborn loan is dearer
    than the alternatives by MORE than the alternatives beat parking.

    In practice that means roughly 2-3 points dearer than everything else you could attack. A spread
    of a point or two across a loan book never clears that bar; a 24% card among 8% home loans does.
    """
    if next_best_rate_pct is None:                       # nothing else to attack, so saving up is all there is
        return True, "no other loan can take the money, so saving toward this one is the only option"
    cost = next_best_rate_pct - parking_rate_pct         # given up per year, per rupee parked
    gain = foreclose_rate_pct - next_best_rate_pct       # earned per year, per rupee, once it closes
    if gain > cost:
        return True, (f"{foreclose_rate_pct:.2f}% beats the next target ({next_best_rate_pct:.2f}%) by "
                      f"{gain:.2f} pts, more than the {cost:.2f} pts given up parking at "
                      f"{parking_rate_pct:.1f}% — saving up wins")
    return False, (f"parking at {parking_rate_pct:.1f}% gives up {cost:.2f} pts against the next target "
                   f"({next_best_rate_pct:.2f}%) to save only {gain:.2f} pts on this one — redirect, and "
                   f"let its own EMI grind it down until it is cheap to close")
