"""The invariants exist because each one already failed once in production data."""

from unittest.mock import patch

from services.consistency import (
    Finding,
    Report,
    check_gold_loans_not_amortised,
    check_leftover_closes_the_waterfall,
    run_all,
)


class _DB:
    def query(self, *a, **k): return self
    def filter(self, *a, **k): return self
    def all(self): return []


def _by(report, name):
    return next(f for f in report.findings if f.check == name)


BASE = dict(income=500000.0, emi=150000.0, fixed=80000.0, saving=50000.0,
            sinking=0.0, insurance=2500.0)


def test_catches_leftover_no_longer_closing_the_waterfall():
    """The regression that put -22,500 on the main screen: leftover redefined as net of spend, so
    cockpit's safe_to_spend subtracts spend a second time."""
    r = Report()
    with patch("api.cashflow.compute_cashflow",
               return_value={**BASE, "leftover": 97500.0, "spent": 120000.0, "spare": -22500.0}), \
         patch("api.budget.compute_budget", return_value={"net_spent_inr": 120000.0}):
        check_leftover_closes_the_waterfall(_DB(), 1, r)
    f = _by(r, "leftover_closes_waterfall")
    assert f.severity == "error"
    assert f.expected == 217500.0 and f.actual == 97500.0


def test_passes_on_the_restored_shape():
    r = Report()
    with patch("api.cashflow.compute_cashflow",
               return_value={**BASE, "leftover": 217500.0, "spent": 120000.0, "spare": 97500.0}), \
         patch("api.budget.compute_budget", return_value={"net_spent_inr": 120000.0}):
        check_leftover_closes_the_waterfall(_DB(), 1, r)
    assert _by(r, "leftover_closes_waterfall").severity == "ok"


def test_catches_spend_drift():
    """cashflow must READ spend from the budget module, not carry its own copy."""
    r = Report()
    with patch("api.cashflow.compute_cashflow",
               return_value={**BASE, "leftover": 217500.0, "spent": 90000.0, "spare": 127500.0}), \
         patch("api.budget.compute_budget", return_value={"net_spent_inr": 120000.0}):
        check_leftover_closes_the_waterfall(_DB(), 1, r)
    f = _by(r, "leftover_closes_waterfall")
    assert f.severity == "error"
    assert f.owner == "api/budget"
    assert f.gap == -30000.0


def test_gold_loan_interest_only_shape_is_NOT_an_error():
    """The critical one. A gold loan whose payment barely covers interest is CORRECT — it is a
    bullet account by design. Flagging it as a defect is what led to a fictional payoff plan being
    presented as fact, and to a peer model's guess being treated as a discovery."""
    class _Gold:
        loan_type, name = "gold", "Example Gold Loan"
        expected_monthly_payment, emi_override, closed = 8000.0, None, False

    db = _DB()
    db.all = lambda: [_Gold()]
    r = Report()
    check_gold_loans_not_amortised(db, 1, r)
    assert _by(r, "gold_loans_not_amortised").severity == "ok"


def test_gold_loan_with_emi_override_is_flagged():
    """An emi_override on a bullet account invites exactly the amortisation maths that must not
    happen to it."""
    class _Gold:
        loan_type, name = "gold", "Example Gold Loan 2"
        expected_monthly_payment, emi_override, closed = 7000.0, 7000.0, False

    db = _DB()
    db.all = lambda: [_Gold()]
    r = Report()
    check_gold_loans_not_amortised(db, 1, r)
    f = _by(r, "gold_loans_not_amortised")
    assert f.severity == "warn"
    assert "emi_override" in f.detail


def test_a_broken_check_does_not_hide_the_others():
    """A failing invariant must be reported, never propagated — this runs on the live app."""
    out = run_all(_DB(), 1)
    assert "findings" in out and out["findings"]
    assert all("check" in f and "severity" in f for f in out["findings"])


def test_finding_gap_is_none_without_both_sides():
    assert Finding("x", "warn", "m", "d").gap is None
    assert Finding("x", "error", "m", "d", expected=100.0, actual=90.0).gap == -10.0
