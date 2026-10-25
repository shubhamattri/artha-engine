"""Payoff-simulation math — three defects found by independent verification.

1. Over-payment on a nearly-cleared loan was DISCARDED instead of cascading to the next loan
   (spare cash vanished across a multi-year plan, so the payoff date was overstated).
2. `interest_saved` was measured against a minimums-only baseline that never terminates, so the
   headline "saves Rs X vs minimum payments" compared a finishing plan to a non-finishing one.
3. A moratorium (flexi) loan's EMI step-up after the moratorium ended was never modelled; the
   interest-only payment was assumed to run for the whole horizon.
"""

from api.debt import _pace_over_budget, _simulate, _strategies


def _loan(loan_id, name, bal, rate, emi, **rest):
    d = {"id": loan_id, "name": name, "balance_inr": bal, "rate_pct": rate, "emi_inr": emi}
    d.update(rest)
    return d


def _avalanche(l):
    return (-l["rate_pct"], l["balance_inr"])


# ── 1. over-payment must cascade ──────────────────────────────────────────────
def test_overpayment_cascades_to_the_next_loan():
    """A needs only ₹5,050 of its ₹10,000 EMI to close in month 1. The unused ₹4,950 must go to B,
    not evaporate. With it, B clears in month 2; while it was discarded, B took 3 months."""
    a = _loan(1, "A", 5_000, 12.0, 10_000)
    b = _loan(2, "B", 15_000, 10.0, 1_000)
    sim = _simulate([a, b], _avalanche, 0.0)
    assert sim["months"] == 2
    assert sim["complete"] is True


def test_cascade_does_not_leak_into_the_minimums_only_baseline():
    """rolldown=False is the honest 'I only pay EMIs' baseline: nothing is redirected, so the unused
    tail of a closing loan's EMI must NOT be pushed onto the next loan (that would be a mini-avalanche)."""
    a = _loan(1, "A", 5_000, 12.0, 10_000)
    b = _loan(2, "B", 15_000, 10.0, 1_000)
    sim = _simulate([a, b], _avalanche, 0.0, rolldown=False)
    assert sim["months"] > 2       # B only ever gets its own ₹1,000/mo


# ── 2. an incomplete baseline can't produce an "interest saved" number ────────
def test_interest_saved_is_none_when_the_baseline_never_completes():
    """₹10L @ 9.6% has interest of exactly ₹8,000/mo. At an ₹8,000 EMI the principal never falls, so
    the minimums-only baseline never terminates and 'saved vs minimums' is not a real quantity."""
    stuck = _loan(1, "Edu", 1_000_000, 9.6, 8_000)
    opt = _strategies([stuck], 50_000.0)["options"][0]
    assert opt["baseline_complete"] is False
    assert opt["interest_saved"] is None
    assert opt["months_saved"] is None


def test_interest_saved_is_real_when_the_baseline_completes():
    ordinary = _loan(1, "PL", 100_000, 12.0, 10_000)
    opt = _strategies([ordinary], 20_000.0)["options"][0]
    assert opt["baseline_complete"] is True
    assert opt["interest_saved"] > 0
    assert opt["months_saved"] >= 0


# ── 3. moratorium EMI step-up must be modelled ───────────────────────────────
def test_flexi_moratorium_emi_stepup_is_modelled():
    """A loan pays interest-only (₹8,000, exactly its interest) during a 24-month
    moratorium, 6 of which have passed, then steps up to ₹25,000. Without the step-up it never
    amortises; with it, it clears."""
    edu = _loan(1, "Edu", 1_000_000, 9.6, 8_000, loan_type="flexi",
                moratorium_months=24, months_passed=6, post_moratorium_emi=25_000)
    stepped = _simulate([edu], _avalanche, 0.0)
    flat = _simulate([_loan(1, "Edu", 1_000_000, 9.6, 8_000)], _avalanche, 0.0)
    assert stepped["complete"] is True
    assert flat["complete"] is False
    assert stepped["months"] < flat["months"]


# ── 4. "am I overspending" must compare like with like (cycle-to-date vs a pro-rated target) ──
def test_on_pace_spending_is_not_flagged_as_overspend():
    """A third of the way through the cycle having spent a third of the budget is ON PACE, not under.
    Comparing cycle-to-DATE spend against the WHOLE-cycle target made early-cycle days look like a huge
    underspend, so 'safe to prepay this cycle' was systematically optimistic."""
    assert _pace_over_budget(38_500, 115_500, days_elapsed=10, days_total=30) == 0.0


def test_spending_ahead_of_pace_is_flagged():
    # the whole month's budget already gone on day 10 of 30 → ₹77,000 ahead of pace
    assert _pace_over_budget(115_500, 115_500, days_elapsed=10, days_total=30) == 77_000.0


def test_full_budget_at_cycle_end_is_on_pace():
    assert _pace_over_budget(115_500, 115_500, days_elapsed=30, days_total=30) == 0.0


def test_underspending_reads_negative():
    assert _pace_over_budget(10_000, 115_500, days_elapsed=10, days_total=30) == -28_500.0


def test_pace_falls_back_to_a_raw_comparison_without_a_cycle():
    assert _pace_over_budget(50_000, 40_000, days_elapsed=0, days_total=0) == 10_000.0


def test_stepup_ignored_when_it_would_lower_the_payment():
    """Only a genuine step-UP is applied; a stale/nonsense smaller post-moratorium figure is ignored."""
    l = _loan(1, "X", 500_000, 10.0, 20_000, moratorium_months=6, months_passed=0,
              post_moratorium_emi=1_000)
    sim = _simulate([l], _avalanche, 0.0)
    assert sim["complete"] is True     # still paying ₹20,000, not dropped to ₹1,000
