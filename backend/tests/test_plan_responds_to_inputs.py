"""Change an input, the payoff plan must move. Nothing between them may be cached or snapshotted.

The plan's monthly figure is leftover minus the variable budget, and leftover is income minus EMIs,
fixed costs, savings, sinking and insurance. Every one of those is a live read. These pin the chain
end to end, because "it recomputes" is easy to believe and easy to get wrong: a single cached value
anywhere in it would leave the app confidently showing a payoff date derived from last month's salary.
"""

from datetime import date, timedelta

from api.cashflow import compute_cashflow
from api.debt import compute_debt
from models.household import CashflowLine, Loan
from models.tenant import Household


def _setup(db):
    h = Household(name="A"); db.add(h); db.commit()
    db.add_all([
        CashflowLine(household_id=h.id, kind="income", label="Salary", amount_inr=200_000),
        CashflowLine(household_id=h.id, kind="fixed", label="Rent", amount_inr=40_000),
        Loan(household_id=h.id, name="Car", lender="X", holder="A", category="Car",
             original_principal=1_000_000, annual_rate_pct=9.0, tenure_months=60,
             disbursement_date=date.today() - timedelta(days=365), rate_type="floating"),
    ])
    db.commit()
    return h


def _plan(db, hid):
    s = compute_debt(db, hid)["strategies"]
    o = next(x for x in s["options"] if x["key"] == "avalanche")
    return s["extra_per_month"], o["months"], o["total_interest"]


def test_a_raise_shortens_the_plan(db_session):
    h = _setup(db_session)
    extra0, months0, interest0 = _plan(db_session, h.id)

    row = db_session.query(CashflowLine).filter_by(household_id=h.id, kind="income").first()
    row.amount_inr = 300_000
    db_session.commit()

    extra1, months1, interest1 = _plan(db_session, h.id)
    assert extra1 > extra0, "a raise did not reach the payoff pool"
    assert months1 <= months0
    assert interest1 <= interest0


def test_a_new_fixed_cost_lengthens_it(db_session):
    h = _setup(db_session)
    extra0, months0, _ = _plan(db_session, h.id)

    db_session.add(CashflowLine(household_id=h.id, kind="fixed", label="School fees",
                                amount_inr=60_000))
    db_session.commit()

    extra1, months1, _ = _plan(db_session, h.id)
    assert extra1 < extra0, "a new fixed cost did not reduce the payoff pool"
    assert months1 >= months0


def test_starting_a_sip_reduces_what_is_left_for_debt(db_session):
    """savings and debt compete for the same rupee, and the plan has to know it."""
    from models.recurring import RecurringInvestment

    h = _setup(db_session)
    extra0, _, _ = _plan(db_session, h.id)

    db_session.add(RecurringInvestment(household_id=h.id, name="SIP", kind="sip", holder="A",
                                       opening_balance=0, as_of_date=date.today(),
                                       monthly_contribution=25_000, annual_rate_pct=12.0,
                                       from_takehome=True, active=True))
    db_session.commit()

    assert _plan(db_session, h.id)[0] < extra0


def test_closing_a_loan_frees_its_emi_into_the_pool(db_session):
    h = _setup(db_session)
    db_session.add(Loan(household_id=h.id, name="Second", lender="Y", holder="A", category="Personal",
                        original_principal=300_000, annual_rate_pct=11.0, tenure_months=36,
                        disbursement_date=date.today() - timedelta(days=365), rate_type="floating"))
    db_session.commit()
    extra0, _, _ = _plan(db_session, h.id)

    second = db_session.query(Loan).filter_by(household_id=h.id, name="Second").first()
    second.closed = True
    db_session.commit()

    assert _plan(db_session, h.id)[0] > extra0, "a closed loan's EMI did not return to the pool"


def test_a_rate_change_moves_the_plan(db_session):
    h = _setup(db_session)
    _, months0, interest0 = _plan(db_session, h.id)

    car = db_session.query(Loan).filter_by(household_id=h.id, name="Car").first()
    car.annual_rate_pct = 14.0
    db_session.commit()

    _, months1, interest1 = _plan(db_session, h.id)
    assert interest1 > interest0, "a rate rise did not reach the payoff plan"


def test_cashflow_and_debt_never_disagree_on_the_same_number(db_session):
    """The silo failure mode: two tabs computing the same figure separately and drifting."""
    h = _setup(db_session)
    cf = compute_cashflow(db_session, h.id)
    s = compute_debt(db_session, h.id)["strategies"]
    assert s["surplus_context"]["free_after_fixed"] == cf["leftover"]
