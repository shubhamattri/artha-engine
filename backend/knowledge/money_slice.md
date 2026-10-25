# Money Slice Contract

Artha's Money Slice defines exactly which transactions count as "spending" in
each context. One consistent definition prevents the Plan, Spending analytics,
and the advisor from reporting different totals for the same period.

## variable_spend — the 10 everyday spend categories

`variable_spend` covers only the 10 everyday discretionary categories:

1. Food & Dining
2. Groceries
3. Transport
4. Shopping
5. Bills & Utilities
6. Health
7. Entertainment & Subscriptions
8. Travel
9. Education
10. Personal / Misc

This is the slice used by the Plan (budget) screen, by `budget_status()`, by
`actually_free()`, and by `spend_by_category()`. Committed fixed flows — EMIs,
credit card bill payments, internal transfers, rent, insurance, savings — are
explicitly excluded because they are accounted for separately in the cashflow
engine.

Never add EMIs or rent to variable_spend totals. Never subtract them from the
variable-spend budget limit. The cashflow engine already captures them in the
`emi` and `fixed` buckets.

## all_household_spend — every spend debit including fixed flows

`all_household_spend` covers ALL debit transactions tagged as `txn_type=SPEND`,
including rent, insurance premiums, and other fixed outflows.

Use this slice ONLY for analytics questions like "what was my total outflow in
June?" — never for budget-vs-actual comparisons. The budget tracks
`variable_spend` only. Comparing `all_household_spend` against a variable-spend
budget will always make the household appear to be massively over-budget.

Label this clearly when using it: "Your total household spend (including rent
and insurance)" not just "your spend".

## Sources — card AND bank account transactions both count

The money slice counts transactions from ALL sources:
- Credit card debits
- Bank account debits (UPI, NEFT, cash withdrawals categorised as SPEND)

There is NO card-only filter. A bank-account SPEND transaction (e.g., a school
fee paid via UPI, or petrol filled via UPI) counts in variable_spend if it
belongs to one of the 10 categories.

The slice only excludes:
- Credit-direction transactions (salary credits, refunds, reversals)
- `txn_type` ≠ SPEND (EMI, card-bill payment, bank transfer, savings, income)
- Duplicate transactions (`dup_of_id IS NOT NULL` — dedup service handles this)
- Archived transactions (`archived = True`)

## Net spent vs gross spent

`net_spent_inr` (from `budget_status()`) = gross variable spend minus any refunds
in the same period. This is the canonical figure to use for all "how much did
I spend" questions.

Refunds reduce net_spent in real time. A purchase followed by a full return in
the same cycle shows ₹0 net spend for that merchant in that period. Always use
`net_spent_inr`, not a gross figure the model computes by ignoring refunds.

`actually_free_inr` uses `net_spent_inr` as its `spent_this_period_inr`, so
refunds are already reflected in the free-cash calculation automatically.

## Deduplication — same-day paise-precise duplicate SMS are merged

The dedup service (`services/dedup.py`) collapses same-day, paise-precise,
same-card, same-merchant duplicate transactions into one row. The variable_spend
total therefore never double-counts a transaction that arrived twice (e.g., a
Zomato order that generated two SMS alerts).

This is a known-good invariant: the advisor must not question whether a specific
transaction was double-counted. If a user reports a suspicious duplicate, direct
them to the Transactions screen to verify — do not invent an explanation.
