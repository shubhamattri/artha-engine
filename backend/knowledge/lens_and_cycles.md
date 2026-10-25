# Lens and Billing Cycle Definitions

Artha tracks variable spend through two interchangeable lenses. The advisor must
always state which lens it used, and must never mix figures from different lenses
in the same calculation.

## Cycle lens — per-card billing cycle (canonical default)

The `cycle` lens buckets each transaction into the billing cycle of the card
that generated it. Card-specific cycle start days in Artha:
- ICICI cards: 12th of the month (cycle runs 12th → 11th of next month)
- HDFC, Axis, Kotak cards: 22nd of the month (cycle runs 22nd → 21st)
- Non-card spend (UPI, bank account, cash): uses the household's default start
  day (settings.cycle_start_day, default 12).

The cycle lens is the CANONICAL lens for all "can I spend more?" and "how much
do I have left?" questions. It matches what the user sees on their credit card
bill and on the Plan screen. When a user says "this month", "this period",
"right now", or "today" without specifying, default to the cycle lens.

Call `budget_status(lens='cycle')` and `actually_free(lens='cycle')` for all
cycle-lens figures. The response includes `period_start` and `period_end` so
the advisor can label the window precisely (e.g., "12 Jun – 11 Jul").

## Month lens — calendar month (1st to last day)

The `month` lens buckets all transactions from the 1st to the last day of the
calendar month regardless of card. Useful for:
- Comparing months in a consistent calendar-aligned way
- Income-aligned budgeting (salaries typically credit on a calendar-month basis)
- Answering "how much did I spend in June?" retrospectively

When a user explicitly says "this calendar month", "since the 1st", or asks for
a month-by-month comparison, use the month lens.

Call `budget_status(lens='month')` and `actually_free(lens='month')`.

## When to use cycle vs month

Default to `cycle` for all current-period / can-I-spend questions. The billing
cycle is the household's real financial month — money flows in and out on card
billing dates, not calendar dates.

Switch to `month` only when:
1. The user explicitly requests calendar-month figures.
2. The question is about a past calendar month (e.g., "how much did I spend in May").
3. Income comparison is the point (salary credit is calendar-month-aligned).

Always state which lens you are using:
- Cycle: "In your current billing cycle (12 Jun – 11 Jul)..."
- Month: "For this calendar month (July 1–31)..."

Never mix cycle and month figures in a single sentence or calculation. If both
lenses are relevant, call both tools and present them separately.

## Days remaining and period bounds

`budget_status()` and `actually_free()` both return:
- `period_start`: ISO date when the current cycle/month began
- `period_end`: ISO date when it ends (inclusive last day)
- `cycle.days_left`: how many days remain in this period

Use these to give concrete time context: "You have 8 days left in this cycle."
Never invent the cycle dates — always read them from the tool response.

## Multi-card cycle handling

For a household with ICICI (12th cycle) and HDFC (22nd cycle) cards, each card's
transactions are in different cycle windows simultaneously. The `instrument_cycle`
mode in the money-slice engine handles this automatically: all cards' CURRENT
cycle transactions (offset 0 from today's card-specific start) are combined into
the "this cycle" bucket. The advisor does not need to account for this manually —
`actually_free(lens='cycle')` already aggregates correctly.
