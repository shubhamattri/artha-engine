"""A skipped EMI must push the payoff date OUT, not be invisible.

compute_state derives a balance from months elapsed, so it assumes every scheduled EMI was paid. Miss
one and the app showed a lower balance and an earlier date than reality, with nothing to say
otherwise. A prepayment and a skipped EMI are mirror images and belong in one ledger.
"""

from datetime import date, timedelta

from models.household import Loan
from models.loan_payment import LoanPayment
from models.tenant import Household
from services import loan_obligation
from services.adherence import measure


def _car(payments=None):
    l = Loan(name="Car", lender="X", holder="A", category="Car",
             original_principal=1_000_000, annual_rate_pct=9.0, tenure_months=60,
             disbursement_date=date.today() - timedelta(days=365))
    l.payments = payments or []
    return l


def test_a_skipped_emi_raises_the_balance():
    clean, _ = loan_obligation.state_of(_car())
    skipped, _ = loan_obligation.state_of(_car([
        LoanPayment(date=date.today(), amount=20_000, kind="skipped_emi")]))
    assert skipped.current_balance > clean.current_balance


def test_it_pushes_the_closure_date_out():
    clean, _ = loan_obligation.state_of(_car())
    skipped, _ = loan_obligation.state_of(_car([
        LoanPayment(date=date.today(), amount=20_000, kind="skipped_emi")] * 6))
    assert skipped.months_left > clean.months_left


def test_a_skip_and_a_prepayment_cancel_out():
    """They are mirror images, so the same amount either way nets to nothing."""
    both, _ = loan_obligation.state_of(_car([
        LoanPayment(date=date.today(), amount=50_000, kind="extra"),
        LoanPayment(date=date.today(), amount=50_000, kind="skipped_emi")]))
    clean, _ = loan_obligation.state_of(_car())
    assert abs(both.current_balance - clean.current_balance) < 1


def test_a_row_with_no_kind_still_counts_as_a_prepayment():
    """Every row written before the column existed."""
    legacy, _ = loan_obligation.state_of(_car([LoanPayment(date=date.today(), amount=50_000)]))
    explicit, _ = loan_obligation.state_of(_car([
        LoanPayment(date=date.today(), amount=50_000, kind="extra")]))
    assert legacy.current_balance == explicit.current_balance


def test_a_skip_is_not_counted_as_an_extra_payment_by_adherence(db_session):
    """It is the opposite of one. Counting it would report a household as following the plan
    BECAUSE it missed an EMI."""
    h = Household(name="A"); db_session.add(h); db_session.commit()
    db_session.add(LoanPayment(loan_id=1, household_id=h.id, date=date(2026, 6, 10),
                               amount=100_000, kind="skipped_emi"))
    db_session.commit()
    r = measure(db_session, h.id, planned_extra=100_000, today=date(2026, 8, 11))
    assert r["actual_avg"] == 0.0
    assert r["status"] == "off_plan"


def test_the_endpoint_defaults_to_the_loans_own_emi(db_session):
    from api.debt import SkipIn, get_loan_detail, skip_emi

    h = Household(name="A"); db_session.add(h); db_session.commit()
    car = Loan(household_id=h.id, name="Car", lender="X", holder="A", category="Car",
               original_principal=1_000_000, annual_rate_pct=9.0, tenure_months=60,
               disbursement_date=date.today() - timedelta(days=365))
    db_session.add(car); db_session.commit()
    before = get_loan_detail(car.id, db=db_session, household=h)

    skip_emi(car.id, SkipIn(date=date.today()), db=db_session, household=h)
    after = get_loan_detail(car.id, db=db_session, household=h)

    assert after["payments"][-1]["kind"] == "skipped_emi"
    assert abs(after["payments"][-1]["amount"] - before["emi_inr"]) < 1
    assert after["balance_inr"] > before["balance_inr"]
