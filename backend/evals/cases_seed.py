"""\
Seeded eval dataset for the Artha advisor (T8 — EM·Eval&Quality).

This module is the authoritative source of the P0 regression cases that MUST pass
every run before any advisor change is merged.  The two real bugs that motivated the
architecture rebuild are LOCKED P0 cases here:

  P0-BRIEFCASE  — advisor called the gross variable-spend pool "free headroom" while
                   the household was over budget.  Correct: actually_free = pool −
                   spent_this_period ≈ ₹5,000, NOT the ₹150,000 pool.

  P0-CAR-EMI    — advisor must size any EMI figure via the emi_quote tool (not
                   self-computation) and give concrete funding routes; when the car
                   price is not given it must ask or state an assumption, not silently
                   invent a number.

Priority semantics:
  P0 — gate is p0_pass_rate=1.0  → ALL P0 cases must pass every run
  P1 — gate is 0.90 overall faithfulness
  P2 — informational / quality signal only

Assertion kinds (T6 runner vocabulary — see evals/schema.py):
  All assertions use the canonical T6 kinds so _check_assertion() dispatches correctly.
  The old T8-invented "t7"/"t8" meta-kinds have been replaced.

  "must_call_tool"      tool: str  — advisor must have called the named tool
  "no_gross_pool_as_free"          — T7: no gross-pool-as-free violation
  "no_contradiction"               — T7: no over-budget + plenty-free contradiction
  "number_check_passed"            — T7: zero violations (hallucinated numbers, etc.)
  "must_contain"        pattern: str  — answer text contains pattern (case-insensitive)
  "assume_or_ask"                  — answer explicitly assumes or asks for missing input
  "judge_pass"          threshold: float  — T8 judge must pass at threshold
  "judge_faithfulness"  min: float       — T8 judge faithfulness score >= min
  "dedup_check"         amounts: list[float], merchant: str, same_card: bool

Data-integrity case (p0_dedup_zomato):
  question="" (empty string, not None — T6 schema requires str).
  _is_dedup_only() → True → T6 runner skips the LLM call entirely, evaluating
  only the dedup_check assertions directly against the dedup service.

  amounts=[531.09, 531.09, 566.73, 566.73]: adjacent same-value pairs so that
  _run_dedup_assertion() tests _same_source_certain() for each pair — the guard
  is a REAL dedup check, not a vacuous paise-precision pass.

Module-level self-validation:
  _validate_cases_seed() runs at import time.  If any assertion uses an unknown kind
  or is missing a required field, the import raises ValueError so CI fails immediately
  rather than silently running cases that would always produce False assertion results.
"""

from __future__ import annotations

try:
    from evals.schema import EvalCase
except ImportError:
    # Fallback stub used when T6's schema.py is not yet available.
    # question: str keeps parity with the real schema (empty string, not None,
    # for dedup-only sentinels).
    from dataclasses import dataclass, field as _field

    @dataclass  # type: ignore[no-redef]
    class EvalCase:  # type: ignore[no-redef]
        id: str
        priority: str
        question: str
        lens: str
        assertions: list = _field(default_factory=list)
        tags: list = _field(default_factory=list)


# ── Assertion-dict builder in T6 vocabulary ───────────────────────────────────

def _a(kind: str, description: str = "", **kwargs: object) -> dict:
    """Build a typed assertion dict compatible with the T6 runner.

    Args:
        kind:        One of the T6-recognised assertion kinds (see module docstring).
        description: Human-readable explanation (stored as metadata; runner ignores it).
        **kwargs:    Kind-specific fields consumed by runner._check_assertion():
                       must_call_tool      → tool: str
                       must_contain        → pattern: str
                       judge_pass          → threshold: float  (default 0.85 in runner)
                       judge_faithfulness  → min: float
                       dedup_check         → amounts: list[float], merchant: str,
                                             same_card: bool
    """
    d: dict = {"kind": kind}
    if description:
        d["description"] = description
    d.update(kwargs)
    return d


# ── P0 — ALL MUST PASS every run ─────────────────────────────────────────────

_P0_BRIEFCASE = EvalCase(
    id="p0_free_cash_briefcase",
    priority="P0",
    question="Can I buy a small briefcase for my kid, like ₹3,000?",
    lens="cycle",
    assertions=[
        _a("must_call_tool",
           description=(
               "Advisor must call actually_free() to get the honest free-cash figure — "
               "not present the gross pool as available."
           ),
           tool="actually_free"),
        _a("no_gross_pool_as_free",
           description=(
               "The ₹150k bug class: the answer must not claim the gross variable-spend "
               "pool (cashflow leftover) is the amount freely available to spend."
           )),
        _a("no_contradiction",
           description=(
               "If the answer says 'over budget' it must NOT also say 'plenty of free "
               "cash' or 'plenty of room' — these directly contradict each other."
           )),
        _a("judge_pass",
           description=(
               "Must give a concrete go/hold verdict for the ₹3,000 briefcase relative "
               "to actually_free — not just refuse or warn without a verdict. "
               "Must not invent undisclosed figures; price (₹3,000) is given."
           ),
           threshold=0.85),
    ],
)

_P0_CAR_BROTHER_EMI = EvalCase(
    id="p0_car_brother_emi",
    priority="P0",
    question="Can I buy a car for my brother? What would the EMI be and how do I pay it?",
    lens="month",
    assertions=[
        _a("must_call_tool",
           description=(
               "Any EMI figure cited must come from emi_quote() — the only sanctioned "
               "tool for loan arithmetic.  Reckoner discipline: never self-compute EMI."
           ),
           tool="emi_quote"),
        _a("number_check_passed",
           description=(
               "Cited EMI must match emi_quote() output within tolerance. "
               "Sanity: EMI × months > principal."
           )),
        _a("assume_or_ask",
           description=(
               "Car price is not given. Advisor must ask ('what's your budget for the "
               "car?') OR label its assumption ('assuming a ₹8L car…'). "
               "Silently using a specific figure without labelling = faithfulness failure."
           )),
        _a("judge_pass",
           description=(
               "Advisor must name at least one concrete funding route (free headroom, "
               "close a high-rate loan, liquidate an asset) and reason through HOW to "
               "make the purchase work — not just 'cars are expensive'."
           ),
           threshold=0.85),
    ],
)

_P0_LENS_CYCLE = EvalCase(
    id="p0_cycle_vs_month_lens_cycle",
    priority="P0",
    question="How much can I still spend this period?",
    lens="cycle",
    assertions=[
        _a("must_call_tool",
           description="Must call actually_free(lens='cycle') for period headroom.",
           tool="actually_free"),
        _a("number_check_passed",
           description=(
               "The actually_free figure cited must match the cycle-lens recompute "
               "within tolerance."
           )),
        _a("must_contain",
           description=(
               "Answer must name or imply the billing-cycle lens "
               "('billing cycle', 'this cycle', 'from the 12th', etc.)."
           ),
           pattern="cycle"),
    ],
)

_P0_LENS_MONTH = EvalCase(
    id="p0_cycle_vs_month_lens_month",
    priority="P0",
    question="How much can I still spend this period?",
    lens="month",
    assertions=[
        _a("must_call_tool",
           description="Must call actually_free(lens='month') for period headroom.",
           tool="actually_free"),
        _a("number_check_passed",
           description=(
               "The actually_free figure cited must match the month-lens recompute "
               "within tolerance."
           )),
        _a("must_contain",
           description=(
               "Answer must name or imply the calendar-month lens "
               "('this month', 'month to date', 'MTD', 'calendar month', etc.)."
           ),
           pattern="month"),
    ],
)

_P0_HALLUCINATED_BALANCE = EvalCase(
    id="p0_hallucinated_balance",
    priority="P0",
    question="What's my exact ICICI loan balance and should I close it?",
    lens="cycle",
    assertions=[
        _a("must_call_tool",
           description="Must call debt_snapshot() to fetch real loan balance, rate, and EMI.",
           tool="debt_snapshot"),
        _a("number_check_passed",
           description=(
               "Every balance, rate, and EMI cited must match debt_snapshot() within "
               "tolerance — any invented figure is a T7 violation."
           )),
        _a("judge_pass",
           description=(
               "Recommendation must reference highest-rate-first (avalanche) using the "
               "actual rate, and give a concrete close/hold verdict with the specific "
               "financial impact — not just 'loans are expensive'."
           ),
           threshold=0.85),
    ],
)

_P0_OVERBUDGET_CONSISTENCY = EvalCase(
    id="p0_overbudget_consistency",
    priority="P0",
    question="I'm over budget this cycle but I really want to buy a ₹40,000 gadget — can I?",
    lens="cycle",
    assertions=[
        _a("must_call_tool",
           description=(
               "Must call actually_free(lens='cycle') to confirm over-budget state before "
               "giving any spend verdict."
           ),
           tool="actually_free"),
        _a("no_gross_pool_as_free",
           description=(
               "Must NOT green-light the ₹40k purchase by pointing at the gross pool. "
               "The pool is the budget, not spare cash."
           )),
        _a("no_contradiction",
           description=(
               "Cannot say 'over budget' AND 'you have ₹X free' in the same answer. "
               "If over budget, actually_free is zero or negative — no free headroom exists."
           )),
        _a("judge_pass",
           description=(
               "₹40k when over budget must be framed as a CAPITAL decision. "
               "Advisor must name a specific path (stagger into next cycle, borrow, "
               "wait until headroom recovers) — not just 'you can't afford it'."
           ),
           threshold=0.85),
    ],
)

_P0_DEDUP_ZOMATO = EvalCase(
    id="p0_dedup_zomato",
    priority="P0",
    # Empty string, not None: T6's EvalCase requires question: str.
    # _is_dedup_only() returns True (all assertions are dedup_check) so the T6
    # runner skips the LLM call entirely and never accesses case.question.
    question="",
    lens="cycle",
    assertions=[
        _a("dedup_check",
           # amounts has each value DUPLICATED so _run_dedup_assertion() hits the
           # "abs(a1 - a2) < 0.01" branch and actually calls _same_source_certain()
           # for each pair.  Using two DIFFERENT amounts (531.09 and 566.73) that
           # are each ingested twice tests both Zomato dedup pairs — this is a REAL
           # check, not a vacuous paise-precision pass.
           amounts=[531.09, 531.09, 566.73, 566.73],
           merchant="ZOMATO",
           same_card=True,
           description=(
               "Same-day paise-precise same-card same-merchant double-ingest: Zomato "
               "₹531.09 and ₹566.73, each ingested twice (amounts=[531.09, 531.09, "
               "566.73, 566.73]). services/dedup._same_source_certain() must merge each "
               "identical pair so budget_status/actually_free never double-counts. "
               "dup_of_id on Transaction is the DB-level evidence (dup_of_id column "
               "confirmed in models/transaction.py:85)."
           )),
    ],
)


# ── P1 — fail → faithfulness gate < 0.90 ─────────────────────────────────────

_P1_EMI_SIZING = EvalCase(
    id="p1_emi_quote_basic",
    priority="P1",
    question="What would the EMI be for a ₹15 lakh personal loan at 12% over 5 years?",
    lens="cycle",
    assertions=[
        _a("must_call_tool",
           description=(
               "EMI must come from emi_quote(principal=1500000, annual_pct=12, months=60). "
               "Sanity: EMI × 60 > 1500000."
           ),
           tool="emi_quote"),
        _a("number_check_passed",
           description=(
               "Cited EMI and total-interest figures must match emi_quote() output within "
               "tolerance."
           )),
        _a("judge_pass",
           description=(
               "Must give the actual EMI number and total interest — not just "
               "'personal loans are expensive'."
           ),
           threshold=0.80),
    ],
)

_P1_DEBT_AVALANCHE = EvalCase(
    id="p1_debt_avalanche_payoff",
    priority="P1",
    question="Which loan should I close first to save the most money?",
    lens="cycle",
    assertions=[
        _a("must_call_tool",
           description="Must call debt_snapshot() for real balances, rates, and EMIs.",
           tool="debt_snapshot"),
        _a("number_check_passed",
           description=(
               "Loan names, balances, and rates cited must match debt_snapshot() within "
               "tolerance — no hallucinated figures."
           )),
        _a("judge_pass",
           description=(
               "Must recommend highest-rate-first (avalanche) with the specific loan "
               "named and approximate interest saved — a concrete ranked recommendation."
           ),
           threshold=0.85),
    ],
)

_P1_SAFE_TO_SPEND = EvalCase(
    id="p1_safe_to_spend_everyday",
    priority="P1",
    question="Is it okay to spend another ₹5,000 on groceries this week?",
    lens="cycle",
    assertions=[
        _a("must_call_tool",
           description=(
               "Must call actually_free() to compare ₹5,000 against remaining headroom — "
               "gross pool alone does not answer this."
           ),
           tool="actually_free"),
        _a("no_gross_pool_as_free",
           description=(
               "Approval must be based on actually_free, not the gross pool. "
               "Citing pool as the approval basis is the ₹150k bug class."
           )),
        _a("judge_pass",
           description=(
               "Must give a clear yes/no with the specific headroom figure — "
               "not just 'monitor your spending'."
           ),
           threshold=0.80),
    ],
)

_P1_NETWORTH_SUMMARY = EvalCase(
    id="p1_networth_snapshot",
    priority="P1",
    question="What's my total net worth right now?",
    lens="cycle",
    assertions=[
        _a("must_call_tool",
           description="Must call networth_snapshot() for live asset figures.",
           tool="networth_snapshot"),
        _a("number_check_passed",
           description=(
               "Every asset and total figure must match networth_snapshot() within "
               "tolerance — no hallucinated balances."
           )),
        _a("judge_pass",
           description=(
               "Must give actual total and by-type breakdown — not just "
               "'your assets are growing'."
           ),
           threshold=0.80),
    ],
)

_P1_CASHFLOW_POOL_FRAMING = EvalCase(
    id="p1_cashflow_pool_framing",
    priority="P1",
    question="How much money do I have to spend on fun stuff each month?",
    lens="cycle",
    assertions=[
        _a("must_call_tool",
           description=(
               "Must call cashflow_snapshot() for the gross pool — gross pool alone does "
               "not answer 'fun money'; must also call actually_free()."
           ),
           tool="cashflow_snapshot"),
        _a("must_call_tool",
           description=(
               "Must also call actually_free() — the gross pool (leftover) is the whole "
               "variable-spend budget, not spare cash. Both figures needed."
           ),
           tool="actually_free"),
        _a("no_gross_pool_as_free",
           description=(
               "Must not cite leftover_inr (gross pool) as the amount for discretionary "
               "spending — that is the full budget, most of which is already committed."
           )),
        _a("judge_faithfulness",
           description=(
               "Must give BOTH gross pool AND actually_free so the user understands why "
               "the freely-spendable amount is smaller than the pool."
           ),
           min=0.80),
    ],
)


# ── P2 — informational quality signal ─────────────────────────────────────────

_P2_VAGUE_SPEND_QUERY = EvalCase(
    id="p2_vague_spend_query",
    priority="P2",
    question="How's my spending lately?",
    lens="cycle",
    assertions=[
        _a("must_call_tool",
           description=(
               "Should call spend_by_category() or budget_status() for a real summary."
           ),
           tool="spend_by_category"),
        _a("judge_pass",
           description=(
               "Must give a real spend summary with top categories named and amounts — "
               "not just 'keep tracking your spending'."
           ),
           threshold=0.75),
    ],
)

_P2_SAVING_ADVICE = EvalCase(
    id="p2_savings_advice",
    priority="P2",
    question="Should I be saving more money?",
    lens="month",
    assertions=[
        _a("must_call_tool",
           description=(
               "Should call cashflow_snapshot() to reference current savings allocation."
           ),
           tool="cashflow_snapshot"),
        _a("judge_pass",
           description=(
               "Must give a concrete opinion grounded in the household's actual savings "
               "rate — not just 'saving more is always good'."
           ),
           threshold=0.75),
    ],
)


# ── Case registry ─────────────────────────────────────────────────────────────

CASES: list[EvalCase] = [
    # P0 — gate is 1.0: ALL must pass
    _P0_BRIEFCASE,
    _P0_CAR_BROTHER_EMI,
    _P0_LENS_CYCLE,
    _P0_LENS_MONTH,
    _P0_HALLUCINATED_BALANCE,
    _P0_OVERBUDGET_CONSISTENCY,
    _P0_DEDUP_ZOMATO,
    # P1 — gate is overall faithfulness >= 0.90
    _P1_EMI_SIZING,
    _P1_DEBT_AVALANCHE,
    _P1_SAFE_TO_SPEND,
    _P1_NETWORTH_SUMMARY,
    _P1_CASHFLOW_POOL_FRAMING,
    # P2 — informational
    _P2_VAGUE_SPEND_QUERY,
    _P2_SAVING_ADVICE,
]


# ── Accessors ─────────────────────────────────────────────────────────────────

def cases_by_priority(priority: str) -> list[EvalCase]:
    return [c for c in CASES if c.priority == priority]


def p0_cases() -> list[EvalCase]:
    return cases_by_priority("P0")


def lm_cases() -> list[EvalCase]:
    """All cases that require an LLM advisor call (excludes dedup-only sentinels).

    A dedup-only case has question='' and all assertions of kind 'dedup_check'.
    Uses truthiness of c.question to exclude them (empty string is falsy).
    """
    return [c for c in CASES if c.question]


# ── Module-level self-validation ──────────────────────────────────────────────
#
# Runs at import time so CI fails immediately if an assertion uses an unknown kind,
# instead of silently running cases where every assertion would return False because
# runner._check_assertion() falls through to its "unknown kind" catch-all.

_VALID_ASSERTION_KINDS: frozenset[str] = frozenset({
    # T7 deterministic violation checks
    "number_check_passed",
    "no_gross_pool_as_free",
    "no_contradiction",
    # Tool-call presence checks
    "must_call_tool",
    "must_not_call_tool",
    # Text-pattern checks
    "must_contain",
    "must_not_contain",
    # T8 judge checks
    "judge_pass",
    "judge_faithfulness",
    # Semantic / text pattern
    "assume_or_ask",
    # Data-integrity (dedup-only cases)
    "dedup_check",
})

# Required extra fields per kind (for deeper self-validation)
_KIND_REQUIRED_FIELDS: dict[str, list[str]] = {
    "must_call_tool":     ["tool"],
    "must_not_call_tool": ["tool"],
    "must_contain":       ["pattern"],
    "must_not_contain":   ["pattern"],
    "dedup_check":        ["amounts"],
}


def _validate_cases_seed() -> None:
    """Self-test: assert every assertion in CASES uses a T6-recognised kind.

    Raises ValueError with a full list of offending assertions if any are invalid.
    Called automatically at module load so that a vocabulary mismatch is caught at
    import time rather than producing silent all-False assertion results at eval time.
    """
    errors: list[str] = []
    for case in CASES:
        if not case.assertions:
            errors.append(f"{case.id}: no assertions defined")
            continue
        for i, a in enumerate(case.assertions):
            k = a.get("kind", "")
            if not k:
                errors.append(f"{case.id}[{i}]: assertion is missing the 'kind' field")
            elif k not in _VALID_ASSERTION_KINDS:
                errors.append(
                    f"{case.id}[{i}]: unknown assertion kind {k!r} — "
                    f"recognised kinds: {sorted(_VALID_ASSERTION_KINDS)}"
                )
            else:
                for req in _KIND_REQUIRED_FIELDS.get(k, []):
                    if not a.get(req):
                        errors.append(
                            f"{case.id}[{i}]: assertion kind {k!r} is missing "
                            f"required field {req!r}"
                        )
    if errors:
        raise ValueError(
            "cases_seed.py assertion validation failed — fix before running evals:\n"
            + "\n".join(f"  • {e}" for e in errors)
        )


def validate_cases() -> None:
    """Public entry point for external test code to call _validate_cases_seed()."""
    _validate_cases_seed()


# Run self-validation immediately at import.  Any kind mismatch raises ValueError
# so it shows up as an ImportError in CI rather than mysteriously failing cases.
_validate_cases_seed()
