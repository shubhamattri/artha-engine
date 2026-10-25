"""Indian income-tax rules, to the extent they change what a household should DO with its money.

Why this exists: the payoff engine ranked loans by their headline rate, which is the wrong number.
A 9.5% education loan whose interest is fully deductible costs less than a 9.1% gold loan that gets
no relief at all — so a nominal-rate avalanche tells you to attack your CHEAPEST debt first.

Three things drive the real cost of a loan:

1. THE REGIME. Under the new regime (the default since FY 2023-24) 80C, 80E and 24(b)-for-self-occupied
   simply do not exist. A household on the new regime has NO shield on any of these loans and the
   nominal ranking is already correct. This is the single biggest input and it is not inferable — ask.

2. WHOSE LOAN IT IS. The deduction lands on the borrower's return, so the relief is worth their
   marginal rate. A loan held by a non-earning member is worth nothing in tax even under the old
   regime.

3. WHETHER THE CAP IS ALREADY FULL — the part everyone gets wrong. Section 24(b) caps self-occupied
   interest at ₹2,00,000. If a household already pays more than that, prepaying a home loan reduces
   interest but does NOT reduce the deduction, which stays pinned at the cap. So the MARGINAL rupee
   of interest carries no relief and the loan's effective rate equals its nominal rate. Modelling this
   as "all home loan interest is deductible" understates the cost of every home loan past the first.

Everything here is marginal-decision maths — "what does one more rupee of interest actually cost" —
not a tax return. It does not compute liability and must not be used to file anything.
"""

from __future__ import annotations

from dataclasses import dataclass

REGIME_OLD = "old"
REGIME_NEW = "new"

CESS = 0.04                       # health & education cess, on tax + surcharge

# (upper bound of slab, marginal rate). FY 2026-27.
SLABS_OLD: list[tuple[float, float]] = [
    (250_000, 0.00), (500_000, 0.05), (1_000_000, 0.20), (float("inf"), 0.30),
]
SLABS_NEW: list[tuple[float, float]] = [
    (400_000, 0.00), (800_000, 0.05), (1_200_000, 0.10), (1_600_000, 0.15),
    (2_000_000, 0.20), (2_400_000, 0.25), (float("inf"), 0.30),
]

# (income above which it applies, surcharge rate). The new regime caps surcharge at 25%.
SURCHARGE: list[tuple[float, float, float]] = [   # threshold, old-regime rate, new-regime rate
    (50_00_000, 0.10, 0.10),
    (1_00_00_000, 0.15, 0.15),
    (2_00_00_000, 0.25, 0.25),
    (5_00_00_000, 0.37, 0.25),
]

SECTION_24B_CAP = 200_000         # self-occupied house property interest
SECTION_80E_YEARS = 8             # education loan interest, from the start of repayment


def marginal_rate(annual_income: float, regime: str = REGIME_NEW) -> float:
    """The tax on ONE more rupee of income — which is also the relief on one more rupee of deduction.

    Includes surcharge and cess, because a deduction saves those too. Returns 0.0 for an income below
    the first taxable slab, which is the honest answer for a non-earning loan holder: their deduction
    is worth nothing.
    """
    if annual_income <= 0:
        return 0.0
    slabs = SLABS_OLD if regime == REGIME_OLD else SLABS_NEW
    base = 0.0
    for upper, rate in slabs:
        if annual_income <= upper:
            base = rate
            break
    if base == 0.0:
        return 0.0
    sur = 0.0
    for threshold, old_rate, new_rate in SURCHARGE:
        if annual_income > threshold:
            sur = old_rate if regime == REGIME_OLD else new_rate
    return base * (1 + sur) * (1 + CESS)


@dataclass
class LoanTax:
    name: str
    nominal_rate_pct: float
    effective_rate_pct: float
    section: str | None           # "80E" | "24(b)" | None
    shielded_fraction: float      # 0..1 — how much of THIS loan's interest gets relief at the margin
    marginal_rate: float          # the holder's marginal tax rate
    why: str                      # plain-language reason, shown in the UI

    @property
    def saves_pct(self) -> float:
        return round(self.nominal_rate_pct - self.effective_rate_pct, 3)


def _annual_interest(balance: float, rate_pct: float) -> float:
    return balance * rate_pct / 100.0


def effective_rates(
    loans: list[dict],
    income_by_holder: dict[str, float],
    regime: str = REGIME_NEW,
    *,
    let_out_holders: set[str] | None = None,
) -> list[LoanTax]:
    """Effective (after-relief) rate per loan, correctly sharing the 24(b) cap within each holder.

    `loans` need: name, balance_inr, rate_pct, category, holder.
    `let_out_holders` names holders whose house property is let out — there the 24(b) cap does not
    apply, so the full interest stays deductible.
    """
    let_out = let_out_holders or set()
    out: list[LoanTax] = []

    # 24(b) is a per-taxpayer cap across ALL their home loans, so group before deciding anything.
    home_interest_by_holder: dict[str, float] = {}
    for l in loans:
        if (l.get("category") or "").lower().startswith("home"):
            h = l.get("holder") or "Household"
            home_interest_by_holder[h] = home_interest_by_holder.get(h, 0.0) + _annual_interest(
                l.get("balance_inr", 0.0), l.get("rate_pct", 0.0))

    for l in loans:
        name = l.get("name", "?")
        nominal = l.get("rate_pct", 0.0)
        holder = l.get("holder") or "Household"
        cat = (l.get("category") or "").lower()
        mrate = marginal_rate(income_by_holder.get(holder, 0.0), regime)

        section: str | None = None
        shielded = 0.0
        why = "no deduction applies — this rate is already the real cost"

        if regime == REGIME_NEW:
            why = "new tax regime: 80C/80E/24(b) are not available, so nothing is sheltered"
        elif mrate <= 0:
            why = f"{holder} has no taxable income, so a deduction is worth nothing"
        elif cat.startswith("education"):
            # 80E has NO cap, so every rupee of interest is relieved — for 8 years from repayment start.
            section, shielded = "80E", 1.0
            why = "80E: education loan interest is fully deductible (no cap, 8 years)"
        elif cat.startswith("home"):
            total = home_interest_by_holder.get(holder, 0.0)
            if holder in let_out:
                section, shielded = "24(b)", 1.0
                why = "24(b): let-out property, so the whole interest stays deductible"
            elif total <= SECTION_24B_CAP:
                section, shielded = "24(b)", 1.0
                why = f"24(b): total home interest ₹{total:,.0f} is within the ₹{SECTION_24B_CAP:,} cap"
            else:
                # THE CAP IS FULL. Cutting this loan's interest does not cut the deduction, which stays
                # pinned at the cap — so the marginal rupee gets no relief at all.
                section, shielded = "24(b) (capped)", 0.0
                why = (f"24(b) cap already full — ₹{total:,.0f} of home interest against a "
                       f"₹{SECTION_24B_CAP:,} cap, so prepaying this saves no tax")

        effective = nominal * (1 - mrate * shielded)
        out.append(LoanTax(name, round(nominal, 3), round(effective, 3), section,
                           shielded, round(mrate, 4), why))
    return out


def payoff_order(
    loans: list[dict],
    income_by_holder: dict[str, float],
    regime: str = REGIME_NEW,
    *,
    let_out_holders: set[str] | None = None,
) -> list[dict]:
    """Loans ranked by EFFECTIVE rate — the correct avalanche. Highest real cost dies first."""
    tax = {t.name: t for t in effective_rates(loans, income_by_holder, regime,
                                              let_out_holders=let_out_holders)}
    ranked = []
    for l in loans:
        t = tax[l.get("name", "?")]
        ranked.append({**l,
                       "effective_rate_pct": t.effective_rate_pct,
                       "tax_section": t.section,
                       "tax_saves_pct": t.saves_pct,
                       "tax_why": t.why})
    ranked.sort(key=lambda x: (-x["effective_rate_pct"], -x.get("balance_inr", 0)))
    return ranked
