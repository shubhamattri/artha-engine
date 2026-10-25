# Affordability and Purchase Decision Framework

The advisor must distinguish everyday spend decisions from capital decisions.
Confusing the two (treating a capital purchase as if it were an everyday spend)
is the second most common advisor failure mode after the gross-pool bug.

## Spend vs capital decision threshold

A purchase is an EVERYDAY SPEND decision if:
- `purchase_amount` ≤ `actually_free_inr` (it fits within remaining pool), AND
- `purchase_amount` < one month's leftover pool (one cycle can absorb it)

A purchase is a CAPITAL decision if any of the following:
- `purchase_amount` > `actually_free_inr` (it would make the period go over budget)
- `purchase_amount` ≥ max(₹2,00,000, 3 × monthly income) — the "big ticket" threshold
- The purchase requires a loan or asset liquidation

Call `affordability(amount, lens)` which auto-detects the regime and routes to
the correct calculation branch. Never decide spend-vs-capital by inspection alone.

## Everyday spend verdict — go / tight / hold

For purchases below the big-ticket threshold, `affordability()` returns a verdict:

- **go**: `actually_free − amount ≥ 15% of pool`. Comfortable — the household
  has meaningful buffer after the purchase.
- **tight**: `actually_free − amount ≥ 0` but `< 15% of pool`. The household
  can do it but the period will be very stretched. Flag this explicitly.
- **hold**: `actually_free − amount < 0`. The purchase exceeds what's actually
  free. Do not green-light it as a spend decision. Treat it as a capital decision
  (borrow, liquidate, or stagger).

The 15% buffer threshold prevents the advisor from green-lighting a purchase that
leaves the household with ₹0 buffer for the rest of the cycle.

## Capital decision paths — borrow, liquidate, stagger

When a purchase is a capital decision, walk through these options in order and
give ONE concrete recommendation with actual rupee figures:

1. **Borrow (EMI)**: Call `emi_quote(principal, annual_pct, months)` with a
   realistic rate and tenure. Check whether the new EMI fits under FOIR headroom
   from `debt_snapshot()`. Quote the exact monthly outflow increase.

2. **Liquidate**: Check `networth_snapshot()` for liquid assets (cash, short-term
   FD, gold, equity redeemable soon). If liquid assets exceed the purchase price,
   partial liquidation may be cheaper than a loan. Name the specific asset.

3. **Stagger**: Split the purchase across two billing cycles (pay half now, half
   next cycle). Works for purchases that are 1.5–2× the monthly pool. No interest
   cost but requires supplier flexibility.

Do not give a vague list of all three options. Pick the cheapest-total-cost path
given the household's current rates and liquidity, name it, and explain the trade-off.

## Big-ticket affordability — FOIR and LTV waterfall

For purchases ≥ max(₹2,00,000, 3 × monthly income) (e.g., a car, house, foreign
trip), `affordability(amount, lens)` auto-routes to the big-ticket branch which
runs a FOIR / LTV / liquidity waterfall:

- `max_loan_inr`: maximum loan at current FOIR headroom (banks approve ≤ 50% FOIR)
- `down_payment_inr`: purchase price minus max loan
- `funding`: ordered list of sources (FD/gold/equity) that cover the down payment
- `shortfall_inr`: how much is still uncovered after exhausting liquid assets
- `new_emi_inr`: monthly EMI for the loan portion (from emi_quote)
- `foir_pct`: new FOIR after adding the loan
- `verdict`: go / tight / hold

The advisor must quote every figure from the tool response, never compute or
round independently.

## Prepayment vs investment decision

When the household has spare cash and a high-rate loan: compare the loan's
effective post-tax rate against the expected post-tax return on the alternative
investment. Indian equity long-run return ≈ 11% p.a. (post-tax ≈ 9% after 10%
LTCG). A 12%+ personal loan beats equity returns after tax — prepay first.

For home-loan rates (7–9%): tax deduction on interest (Section 24b, ₹2L/year)
brings the effective rate to 5–6% for salaried taxpayers in the 30% bracket,
which is below long-run equity returns. In that range, continuing the loan and
investing the cash is defensible.

Always note the actual loan rate from `debt_snapshot()` — never use a default
rate in the explanation.
