"""A lender that refuses part payment changes where spare cash can go, so it changes the plan.

Modelling every loan as freely part-payable produces closure dates that cannot happen: the money is
sent at a loan that hands it straight back.
"""

from api.debt import _STRATEGIES, _simulate

AVALANCHE = _STRATEGIES[0][3]


def _loan(name, bal, rate, emi, prepayment="part"):
    return {"id": abs(hash(name)) % 10000, "name": name, "balance_inr": bal, "rate_pct": rate,
            "emi_inr": emi, "prepayment_rule": prepayment, "loan_type": "standard",
            "moratorium_months": 0, "post_moratorium_emi": None, "months_passed": 0}


def test_foreclose_only_refuses_a_part_payment_and_the_cash_moves_on():
    """The pool cannot chip a foreclose-only loan, so it must reach the next one instead of being
    parked or silently destroyed."""
    loans = [_loan("Car (foreclose only)", 800_000, 9.35, 18_000, "foreclose_only"),
             _loan("Home (part ok)", 900_000, 8.10, 9_000, "part")]
    sim = _simulate(loans, AVALANCHE, extra=50_000)
    by = {s["name"]: s for s in sim["steps"]}
    # avalanche puts the car first on rate, but it will not take Rs 50k a month, so the home loan
    # closes first even though it is cheaper debt.
    assert by["Home (part ok)"]["closes_in_months"] < by["Car (foreclose only)"]["closes_in_months"]


def test_a_pool_big_enough_to_clear_it_IS_accepted():
    loans = [_loan("Car (foreclose only)", 200_000, 9.35, 18_000, "foreclose_only")]
    sim = _simulate(loans, AVALANCHE, extra=250_000)
    assert sim["steps"][0]["closes_in_months"] == 1     # one month, cleared outright


def test_none_never_takes_early_money_and_runs_its_full_term():
    locked = [_loan("Locked", 500_000, 10.0, 20_000, "none")]
    free = [_loan("Free", 500_000, 10.0, 20_000, "part")]
    assert (_simulate(locked, AVALANCHE, extra=100_000)["months"]
            > _simulate(free, AVALANCHE, extra=100_000)["months"])


def test_default_is_permissive_so_existing_loans_are_unchanged():
    """Loans predating the column have no 'prepayment' key at all."""
    legacy = [{"id": 1, "name": "Legacy", "balance_inr": 500_000, "rate_pct": 10.0,
               "emi_inr": 20_000, "loan_type": "standard", "moratorium_months": 0,
               "post_moratorium_emi": None, "months_passed": 0}]
    explicit = [_loan("Explicit", 500_000, 10.0, 20_000, "part")]
    assert (_simulate(legacy, AVALANCHE, extra=100_000)["months"]
            == _simulate(explicit, AVALANCHE, extra=100_000)["months"])


def test_the_cash_is_never_destroyed():
    """Skipping a loan must not delete the money — total interest has to beat doing nothing extra."""
    loans = [_loan("Car", 800_000, 9.35, 18_000, "foreclose_only"),
             _loan("Home", 900_000, 8.10, 9_000, "part")]
    assert (_simulate(loans, AVALANCHE, extra=50_000)["total_interest"]
            < _simulate(loans, AVALANCHE, extra=0.0)["total_interest"])


# ── the DERIVED default: for a floating-rate individual borrower the RBI decides this ──
from services import prepayment  # noqa: E402


class _L:
    def __init__(self, rate_type="fixed", loan_type="standard", category="", prepayment=None,
                 prepayment_charge_pct=None):
        self.rate_type, self.loan_type, self.category = rate_type, loan_type, category
        self.prepayment, self.prepayment_charge_pct = prepayment, prepayment_charge_pct


def test_a_floating_car_loan_is_freely_prepayable():
    """The counter-intuitive one. RBI keys off the RATE TYPE, not the loan category, so a floating
    car loan takes part payments free while a fixed one may not."""
    rule, charge, why = prepayment.derive(_L(rate_type="floating", category="Car"))
    assert rule == prepayment.PART and charge == 0.0
    assert "RBI" in why


def test_a_fixed_car_loan_defaults_to_foreclosure_only():
    rule, charge, _ = prepayment.derive(_L(rate_type="fixed", category="Car"))
    assert rule == prepayment.FORECLOSE_ONLY and charge > 0


def test_a_gold_loan_is_always_open():
    rule, charge, why = prepayment.derive(_L(rate_type="fixed", loan_type="gold", category="Gold Loan"))
    assert rule == prepayment.PART and charge == 0.0
    assert "running account" in why


def test_an_explicit_override_beats_the_derived_default():
    """The sanction letter governs. Deriving is a default, never the last word."""
    loan = _L(rate_type="floating", category="Car", prepayment=prepayment.FORECLOSE_ONLY,
              prepayment_charge_pct=2.0)
    rule, charge, why = prepayment.effective(loan)
    assert rule == prepayment.FORECLOSE_ONLY and charge == 2.0
    assert "explicitly" in why


def test_floating_home_loans_are_free_regardless_of_category():
    for cat in ("Home", "Education", "Personal"):
        rule, charge, _ = prepayment.derive(_L(rate_type="floating", category=cat))
        assert (rule, charge) == (prepayment.PART, 0.0), cat


def test_a_foreclosure_fee_must_be_affordable_before_the_loan_can_close():
    """The pool has to cover the balance AND the lender's fee. Quoting a closure month without the
    fee prices the plan at something the lender will not honour."""
    def car(fee):
        return [{"id": 1, "name": "Car", "balance_inr": 200_000, "rate_pct": 9.35,
                 "emi_inr": 18_000, "prepayment_rule": "foreclose_only",
                 "prepayment_charge_pct": fee, "loan_type": "standard",
                 "moratorium_months": 0, "post_moratorium_emi": None, "months_passed": 0}]
    # 205k pool clears a 200k balance with no fee, but not with a 5% (10k) one.
    assert _simulate(car(0.0), AVALANCHE, extra=187_000)["steps"][0]["closes_in_months"] == 1
    assert _simulate(car(5.0), AVALANCHE, extra=187_000)["steps"][0]["closes_in_months"] > 1


def test_the_fee_is_reported_apart_from_interest():
    """A foreclosure fee is the cost of CHOOSING this plan, not the cost of borrowing. Folding it
    into interest would make one strategy look dearer to lend on than another."""
    loans = [{"id": 1, "name": "Car", "balance_inr": 200_000, "rate_pct": 9.35, "emi_inr": 18_000,
              "prepayment_rule": "foreclose_only", "prepayment_charge_pct": 3.0,
              "loan_type": "standard", "moratorium_months": 0, "post_moratorium_emi": None,
              "months_passed": 0}]
    sim = _simulate(loans, AVALANCHE, extra=300_000)
    assert sim["prepayment_fees"] > 0
    assert sim["total_cost"] == round(sim["total_interest"] + sim["prepayment_fees"], 2)


def test_a_free_to_prepay_loan_incurs_no_fee():
    loans = [{"id": 1, "name": "Home", "balance_inr": 200_000, "rate_pct": 8.10, "emi_inr": 9_000,
              "prepayment_rule": "part", "prepayment_charge_pct": 0.0, "loan_type": "standard",
              "moratorium_months": 0, "post_moratorium_emi": None, "months_passed": 0}]
    assert _simulate(loans, AVALANCHE, extra=300_000)["prepayment_fees"] == 0.0
