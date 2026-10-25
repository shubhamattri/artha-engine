# Artha Financial Definitions

Core definitions the advisor MUST retrieve before making any cash-availability,
loan-sizing, or budget claim. These facts replace training-data intuitions about
"leftover = free cash" with Artha's actual domain semantics.

## Leftover is the variable-spend pool, not free cash

`leftover` in Artha's cashflow engine equals:

    income − EMIs − fixed expenses − savings − sinking funds − insurance premiums

It is the TOTAL monthly budget pool from which ALL variable spending is drawn:
groceries, food, transport, shopping, travel, entertainment, health, utilities,
education, and personal/misc. It is NOT spare cash sitting unused in an account.

By the middle of a billing cycle the pool is already substantially spent. Never
present the gross leftover figure as money the household can freely spend without
checking how much has already been drawn from it this period.

Call `cashflow_snapshot()` to get the pool (`leftover_inr`). The summary field
of that tool explicitly labels it "pool", not "free".

## actually_free — the only correct free-cash figure

`actually_free` is the single honest answer to "how much can I still spend?"

    actually_free = pool (leftover) − net_spent_this_period

Call `actually_free(lens)`. It returns:
- `pool_inr`: the gross monthly variable-spend pool
- `spent_this_period_inr`: what has already been drawn from the pool this cycle/month
- `actually_free_inr`: the net remainder (pool minus spent). This is the only
  number the advisor may present as "free to spend".
- `over_budget`: true when actually_free_inr is negative
- `period_start` / `period_end`: the exact window covered

Example: pool = P; spent this period = S → actually_free = P − S.
If S > P then actually_free is negative and the household is over the pool.
Always derive P and S from the tool — never invent or assume them.

## Gross pool as free is wrong — locked P0 failure

NEVER present the gross variable-spend pool (`leftover_inr`, `pool_inr`) as the
amount the household can spend. This is the highest-priority advisor failure.

Wrong: "You have ₹X free / as headroom / sitting there." (where X equals pool_inr)
Correct: "Your pool is ₹X. You've already spent ₹Y this period,
          leaving ₹(X−Y) actually free."

Wrong: "You're over budget BUT you still have plenty of free cash."
Correct: These two claims contradict each other. If actually_free_inr is negative
         (i.e., the household is over the pool), there is no free cash. State
         the overage clearly: "You're ₹A over your pool for this period."

T7 (the deterministic number verifier) treats any 'free/spare/headroom' figure
equal to the gross pool as a violation and blocks the response as a P0 failure.

## FOIR — Fixed Obligation to Income Ratio

FOIR = (total monthly EMI obligations ÷ monthly gross income) × 100%.

Indian banks enforce a hard FOIR ceiling of 50% when assessing home-loan or
personal-loan eligibility. A household with EMIs already at 40% of income has
only 10% of income remaining under the FOIR cap for new EMI commitments.

FOIR headroom (available for new EMIs):
    foir_room = max(0, 0.50 × income − current_emi_total)

Maximum additional loan the household qualifies for (approximate):
    max_additional_loan = foir_room ÷ EMI-per-rupee-factor

Call `debt_snapshot()` to get current EMI total and `cashflow_snapshot()` for
income. Call `emi_quote(principal, annual_pct, months)` for the EMI-per-rupee
factor at any specific rate and tenure. Never invent FOIR figures — always
derive from those two tools.

## EMI reckoner — reducing-balance math

Standard reducing-balance EMI formula:

    EMI = P × r × (1+r)^n / ((1+r)^n − 1)
    where r = annual_rate / 1200,  n = tenure in months

The advisor MUST NOT compute EMI by hand. Call `emi_quote(principal, annual_pct,
months)` which returns:
- `emi_inr`: the monthly payment (computed deterministically, not by the LLM)
- `total_payment_inr`: emi × months
- `total_interest_inr`: total paid minus principal
- `summary`: a ready-to-quote one-liner

Sanity check (always): emi × months MUST be greater than the principal. If the
tool violates this, something is wrong — raise the contradiction, do not quote
the figure.
