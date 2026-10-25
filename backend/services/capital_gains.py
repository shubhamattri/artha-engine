"""Capital-gains treatment of a disposal: is it long-term, at what rate, and what is actually owed.

Three rules here exist because each one costs real money when it is missed:

1. Repaying a loan out of the proceeds does NOT reduce the gain. It reduces only what reaches your
   bank. A property sold for Rs 80,00,000 that leaves Rs 15,00,000 in hand is still taxed on the gain
   computed from Rs 80,00,000.

2. The holding period that splits long from short term is NOT the same for every asset. Listed Indian
   equity turns long-term at 12 months; property, gold, and foreign shares take 24. A foreign listed
   share (one listed only on a European exchange) counts as UNLISTED for Indian tax, because "listed" means a
   recognised Indian exchange - so 24 months, not 12.

3. Property acquired before 23 July 2024 keeps a CHOICE between 12.5% without indexation and 20% with
   it, and the cheaper one is not always the newer one. On a property bought before
   the cutoff and held a few years, indexation can still be the cheaper method.

Marginal-decision arithmetic for planning. Not a tax return, and the CII figures for the current year
must come from the notification, not from here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

CESS = 0.04

# Months an asset must be held to become long-term.
LONG_TERM_MONTHS = {
    "equity_listed_in": 12,      # Indian listed equity + equity mutual funds
    "equity_foreign": 24,        # foreign shares — unlisted for Indian tax purposes
    "property": 24,
    "gold": 24,
    "debt_fund": None,           # bought after 1 Apr 2023: always slab, no long-term at all
    "other": 24,
}

LTCG_RATE = {
    "equity_listed_in": 0.125,
    "equity_foreign": 0.125,
    "property": 0.125,
    "gold": 0.125,
    "other": 0.125,
}

# Only Indian listed equity gets the annual exemption. Foreign shares and property do not.
LTCG_EXEMPTION = {"equity_listed_in": 125_000.0}

INDEXATION_CUTOFF = date(2024, 7, 23)   # property bought before this keeps the 20%-with-indexation option


@dataclass
class GainResult:
    gain_inr: float | None
    is_long_term: bool | None
    months_held: int | None
    rate_used: float | None
    tax_inr: float | None
    method: str
    why: str


def _months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month) - (1 if b.day < a.day else 0)


def compute_gain(
    *,
    asset_class: str,
    gross_proceeds: float,
    costs: float,
    cost_basis: float | None,
    acquired_on: date | None,
    disposed_on: date,
    slab_rate: float,
    cii_acquired: float | None = None,
    cii_disposed: float | None = None,
) -> GainResult:
    """`costs` = brokerage/stamp duty/legal. Any loan settled from the proceeds is NOT passed here."""
    if cost_basis is None:
        return GainResult(None, None, None, None, None, "unknown",
                          "cost basis unknown — for ESOP shares it is the FMV on each exercise "
                          "date (already taxed as a perquisite), not the discounted price paid")

    gain = round(gross_proceeds - costs - cost_basis, 2)
    if acquired_on is None:
        return GainResult(gain, None, None, None, None, "undated",
                          "acquisition date unknown, so long-term vs short-term cannot be decided")

    months = _months_between(acquired_on, disposed_on)
    threshold = LONG_TERM_MONTHS.get(asset_class, 24)
    if threshold is None:
        tax = max(0.0, gain) * slab_rate
        return GainResult(gain, False, months, slab_rate, round(tax, 2), "slab",
                          "post-Apr-2023 debt: taxed at slab whatever the holding period")

    long_term = months >= threshold
    if not long_term:
        tax = max(0.0, gain) * slab_rate
        return GainResult(gain, False, months, slab_rate, round(tax, 2), "stcg",
                          f"held {months} months, under the {threshold} needed — short-term, taxed at slab")

    rate = LTCG_RATE.get(asset_class, 0.125)
    exempt = LTCG_EXEMPTION.get(asset_class, 0.0)
    taxable = max(0.0, gain - exempt)
    tax_plain = taxable * rate * (1 + CESS)

    # Property bought before the cutoff may use 20% on the INDEXED gain instead, if that is cheaper.
    if (asset_class == "property" and acquired_on < INDEXATION_CUTOFF
            and cii_acquired and cii_disposed):
        indexed_cost = cost_basis * cii_disposed / cii_acquired
        gain_idx = max(0.0, gross_proceeds - costs - indexed_cost)
        tax_idx = gain_idx * 0.20 * (1 + CESS)
        if tax_idx < tax_plain:
            return GainResult(round(gain_idx, 2), True, months, 0.20, round(tax_idx, 2), "ltcg_indexed",
                              f"held {months} months. 20% on the indexed gain beats 12.5% on the raw "
                              f"gain by Rs {tax_plain - tax_idx:,.0f} (indexed cost Rs {indexed_cost:,.0f})")

    return GainResult(gain, True, months, rate, round(tax_plain, 2), "ltcg",
                      f"held {months} months, long-term at {rate*100:.1f}%"
                      + (f" after a Rs {exempt:,.0f} exemption" if exempt else
                         " (no annual exemption applies to this asset class)"))
