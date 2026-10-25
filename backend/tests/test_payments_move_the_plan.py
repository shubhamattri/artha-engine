"""Log a payment and the plan must shift. Otherwise the app takes an entry and silently drops it.

The payments panel was generalised from gold loans to every loan, but only gold read the ledger. A
prepayment recorded against a car or home loan was written to loan_payments and never looked at: the
balance did not move, the closure date did not move, and nothing said so.
"""

from datetime import date, timedelta

from models.household import Loan
from models.loan_payment import LoanPayment
from services import loan_obligation


def _car(payments=None):
    l = Loan(name="Car", lender="X", holder="A", category="Car",
             original_principal=1_000_000, annual_rate_pct=9.0, tenure_months=60,
             disbursement_date=date.today() - timedelta(days=365))
    l.payments = payments or []
    return l


def test_a_logged_prepayment_reduces_a_standard_loan():
    before, _ = loan_obligation.state_of(_car())
    after, _ = loan_obligation.state_of(_car([LoanPayment(date=date.today(), amount=200_000)]))
    assert after.current_balance < before.current_balance - 199_000


def test_it_also_pulls_the_closure_date_in():
    before, _ = loan_obligation.state_of(_car())
    after, _ = loan_obligation.state_of(_car([LoanPayment(date=date.today(), amount=200_000)]))
    assert after.months_left < before.months_left


def test_several_payments_accumulate():
    one = loan_obligation.state_of(_car([LoanPayment(date=date.today(), amount=100_000)]))[0]
    two = loan_obligation.state_of(_car([LoanPayment(date=date.today(), amount=100_000),
                                         LoanPayment(date=date.today(), amount=100_000)]))[0]
    assert two.current_balance < one.current_balance


def test_extra_principal_and_the_ledger_are_added_not_swapped():
    """extra_principal predates the ledger. Reading one INSTEAD of the other would silently drop
    whichever a given loan happens to use."""
    l = _car([LoanPayment(date=date.today(), amount=50_000)])
    l.extra_principal = 50_000
    both, _ = loan_obligation.state_of(l)
    only_ledger, _ = loan_obligation.state_of(_car([LoanPayment(date=date.today(), amount=50_000)]))
    assert both.current_balance < only_ledger.current_balance


def test_no_payments_is_unchanged():
    assert (loan_obligation.state_of(_car())[0].current_balance
            == loan_obligation.state_of(_car([]))[0].current_balance)


def test_every_loan_lists_its_payments_so_they_can_be_removed(db_session):
    """Gating the list on gold made a prepayment on any other loan write-only: creatable, never
    listable, never deletable. Two probe payments got stuck on a live loan exactly this way."""
    from api.debt import PaymentIn, add_payment, delete_payment, get_loan_detail
    from models.tenant import Household

    h = Household(name="A"); db_session.add(h); db_session.commit()
    car = Loan(household_id=h.id, name="Car", lender="X", holder="A", category="Car",
               original_principal=1_000_000, annual_rate_pct=9.0, tenure_months=60,
               disbursement_date=date.today() - timedelta(days=365))
    db_session.add(car); db_session.commit()

    v = add_payment(car.id, PaymentIn(date=date.today(), amount=50_000), db=db_session, household=h)
    assert v["payments"], "a standard loan did not return its payments"
    pid = v["payments"][-1]["id"]

    after = get_loan_detail(car.id, db=db_session, household=h)
    assert any(p["id"] == pid for p in after["payments"])

    delete_payment(car.id, pid, db=db_session, household=h)
    assert not get_loan_detail(car.id, db=db_session, household=h)["payments"]
