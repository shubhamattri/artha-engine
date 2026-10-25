"""The loan invariant, and the three shapes that look broken but are not.

Two of these tests exist only to keep the check quiet: a gold loan servicing interest and an education
loan in moratorium are both CORRECT, and a checker that reports them is worse than no checker — it is
the reasoning that produced a fictional payoff plan, dressed up as a green tick.
"""

from unittest.mock import patch

from services.consistency import CHECKS, Report, check_loans_are_self_consistent


class _Household:
    id = 1


class _Loan:
    """A loan row as the check reads it: only the columns that exist on models.household.Loan."""

    def __init__(self, id=1, name="Loan", original_principal=100000.0, annual_rate_pct=10.0,
                 tenure_months=24, emi_override=None):
        self.id = id
        self.name = name
        self.original_principal = original_principal
        self.annual_rate_pct = annual_rate_pct
        self.tenure_months = tenure_months
        self.emi_override = emi_override


class _DB:
    """query(Household).first() -> the household; query(Loan).all() -> the rows under test."""

    def __init__(self, loans):
        self._loans = loans

    def query(self, *a, **k):
        return self

    def filter(self, *a, **k):
        return self

    def first(self):
        return _Household()

    def all(self):
        return self._loans


def _schedule(n):
    return [{"n": i} for i in range(1, n + 1)]


def _run(loan, view):
    r = Report()
    with patch("api.debt.get_loan_detail", return_value=view):
        check_loans_are_self_consistent(_DB([loan]), 1, r)
    return next(f for f in r.findings if f.check == "loans_self_consistent")


def test_registered_in_the_checks_tuple():
    """An invariant nobody runs is a comment."""
    assert check_loans_are_self_consistent in CHECKS


def test_healthy_standard_loan_passes():
    loan = _Loan(name="Car Loan", original_principal=800000.0, annual_rate_pct=9.0,
                 tenure_months=60)
    view = {
        "name": "Car Loan", "loan_type": "standard", "rate_pct": 9.0,
        "balance_inr": 500000.0, "emi_inr": 16606.68, "months_remaining": 36,
        "months_passed": 24, "tenure_months": 60, "phase": None,
        "forward_schedule": _schedule(36),
    }
    assert _run(loan, view).severity == "ok"


def test_gold_loan_interest_only_shape_is_not_flagged():
    """loan_type='gold' is a bullet account: expected_monthly_payment is a nominal outflow, NOT an EMI,
    and principal legitimately sits still while it services interest. Running the covers-its-interest
    test on one is what produced a payoff date that cannot happen."""
    loan = _Loan(name="Gold Loan", original_principal=500000.0, annual_rate_pct=9.1,
                 tenure_months=12)
    # Paying at or under the accruing interest is the bullet shape: compute_gold_state answers 999
    # ("never") and forward_schedule is empty, because principal is not scheduled down at all.
    view = {
        "name": "Gold Loan", "loan_type": "gold", "rate_pct": 9.1,
        "balance_inr": 512000.0, "principal_outstanding": 500000.0,   # interest = 3,791.67/mo
        "expected_monthly_payment": 3750.0, "emi_inr": 3750.0,
        "months_remaining": 999, "months_passed": 14, "tenure_months": 12,
        "phase": "active", "forward_schedule": [],
    }
    assert _run(loan, view).severity == "ok"


def test_gold_loan_past_its_bullet_tenure_is_still_not_flagged():
    """A gold loan's tenure is a renewal date, not a payoff schedule — they are routinely rolled. A
    warning that fires forever and can never be cleared is a false alarm with extra steps."""
    loan = _Loan(name="Gold Loan 2", original_principal=300000.0, annual_rate_pct=9.0,
                 tenure_months=12)
    view = {
        "name": "Gold Loan 2", "loan_type": "gold", "rate_pct": 9.0,
        "balance_inr": 302000.0, "principal_outstanding": 300000.0,   # interest = 2,250/mo
        "expected_monthly_payment": 2250.0, "emi_inr": 2250.0,
        "months_remaining": 999, "months_passed": 26, "tenure_months": 12,
        "phase": "active", "forward_schedule": [],
    }
    assert _run(loan, view).severity == "ok"


def test_amortising_loan_past_its_tenure_and_still_owing_is_flagged():
    """Unlike a bullet account, an EMI loan past its tenure with a balance is inputs disagreeing with
    the maths — one of the two is stale."""
    loan = _Loan(name="Car Loan", original_principal=800000.0, annual_rate_pct=9.0,
                 tenure_months=60, emi_override=16606.68)
    view = {
        "name": "Car Loan", "loan_type": "standard", "rate_pct": 9.0,
        "balance_inr": 120000.0, "emi_inr": 16606.68, "months_remaining": 8,
        "months_passed": 64, "tenure_months": 60, "phase": None,
        "forward_schedule": _schedule(8),
    }
    f = _run(loan, view)
    assert f.severity == "warn"
    assert "no longer describes this loan" in f.detail


def test_moratorium_loan_is_judged_on_the_emi_it_steps_up_to():
    """An education loan in moratorium pays interest only today. On today's payment it looks like a loan
    that never clears; on post_moratorium_emi it clears fine, which is why the schedule uses steps."""
    loan = _Loan(name="Education Loan", original_principal=1500000.0, annual_rate_pct=9.5,
                 tenure_months=120)
    view = {
        "name": "Education Loan", "loan_type": "flexi", "rate_pct": 9.5,
        "balance_inr": 1200000.0, "emi_inr": 9500.0,          # = the monthly interest, by design
        "post_moratorium_emi": 18000.0, "phase": "moratorium",
        "moratorium_months": 24, "months_remaining": 108, "months_passed": 12,
        "tenure_months": 120, "forward_schedule": _schedule(108),
    }
    assert _run(loan, view).severity == "ok"


def test_never_amortising_loan_is_flagged():
    """A standard loan whose EMI cannot cover its interest, with no later step that can. Its closure
    date is arithmetic on a balance that never falls."""
    loan = _Loan(name="Personal Loan", original_principal=1000000.0, annual_rate_pct=18.0,
                 tenure_months=60, emi_override=10000.0)
    view = {
        "name": "Personal Loan", "loan_type": "standard", "rate_pct": 18.0,
        "balance_inr": 900000.0, "emi_inr": 10000.0, "months_remaining": 48,
        "months_passed": 12, "tenure_months": 60, "phase": None,
        "forward_schedule": [],
    }
    f = _run(loan, view)
    assert f.severity == "error"
    assert "balance never falls" in f.detail


def test_schedule_length_disagreeing_with_months_remaining_is_flagged():
    loan = _Loan(name="Car Loan", original_principal=800000.0, annual_rate_pct=9.0,
                 tenure_months=60, emi_override=16606.68)
    view = {
        "name": "Car Loan", "loan_type": "standard", "rate_pct": 9.0,
        "balance_inr": 500000.0, "emi_inr": 16606.68, "months_remaining": 36,
        "months_passed": 24, "tenure_months": 60, "phase": None,
        "forward_schedule": _schedule(48),
    }
    f = _run(loan, view)
    assert f.severity == "error"
    assert "two payoff dates" in f.detail


def test_derived_emi_must_match_the_formula_on_the_stated_inputs():
    """No emi_override, so emi_inr has one legal value: loan_math.emi on this loan's own inputs."""
    loan = _Loan(name="Car Loan", original_principal=800000.0, annual_rate_pct=9.0,
                 tenure_months=60)
    view = {
        "name": "Car Loan", "loan_type": "standard", "rate_pct": 9.0,
        "balance_inr": 500000.0, "emi_inr": 15000.0, "months_remaining": 36,
        "months_passed": 24, "tenure_months": 60, "phase": None,
        "forward_schedule": _schedule(36),
    }
    f = _run(loan, view)
    assert f.severity == "error"
    assert "formula" in f.detail


def test_no_loans_is_clean_not_broken():
    r = Report()
    with patch("api.debt.get_loan_detail", return_value={}):
        check_loans_are_self_consistent(_DB([]), 1, r)
    assert next(f for f in r.findings if f.check == "loans_self_consistent").severity == "ok"
