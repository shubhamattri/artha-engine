"""A figure built on set-asides ASSUMED to have happened must say so before it is offered as cash.

A sinking fund's balance is opening_balance plus every monthly contribution multiplied by the months
elapsed. Nothing reconciles those contributions against real bank debits, unlike every other recurring
outflow in this app, so a fund created and never funded is indistinguishable from one funded monthly.

That would be a footnote if the number stayed on the Savings page. It does not: services/recovery
offers it as a way to cover an overspend, and its comment used to call it "actual cash already set
aside". Telling a household that is already over budget to draw money nobody ever transferred pushes
them further over — the worst direction for this to be wrong in.
"""

from datetime import date, timedelta

from models.tenant import Household


def _fund(hid: int, **kw):
    from models.sinking import SinkingFund
    defaults = dict(household_id=hid, name="Travel", monthly_contribution=5_000.0,
                    opening_balance=0.0, as_of_date=date.today() - timedelta(days=210),
                    target_annual=60_000.0, holder=None, withdrawals=[], active=True, notes=None)
    defaults.update(kw)
    return SinkingFund(**defaults)


def _household(db_session) -> Household:
    h = Household(name="SF")
    db_session.add(h)
    db_session.commit()
    return h


def test_a_never_funded_bucket_reports_its_whole_balance_as_assumed(db_session):
    """Seven months of a 5,000 set-aside that may never have left the account."""
    from api.sinking import compute_sinking
    h = _household(db_session)
    db_session.add(_fund(h.id))
    db_session.commit()

    fund = compute_sinking(db_session, h.id)["funds"][0]
    assert fund["balance"] > 0
    assert fund["assumed_inr"] == fund["balance"], "nothing here was ever observed"


def test_a_stated_opening_balance_is_not_assumed(db_session):
    """Money the household declared it already had is an observation, not an extrapolation. Only the
    monthly accrual on top of it is assumed."""
    from api.sinking import compute_sinking
    h = _household(db_session)
    db_session.add(_fund(h.id, opening_balance=20_000.0, monthly_contribution=0.0))
    db_session.commit()

    fund = compute_sinking(db_session, h.id)["funds"][0]
    assert fund["balance"] == 20_000.0
    assert fund["assumed_inr"] == 0.0


def test_an_inactive_fund_accrues_nothing_and_assumes_nothing(db_session):
    from api.sinking import compute_sinking
    h = _household(db_session)
    db_session.add(_fund(h.id, opening_balance=8_000.0, active=False))
    db_session.commit()

    fund = compute_sinking(db_session, h.id)["funds"][0]
    assert fund["balance"] == 8_000.0
    assert fund["assumed_inr"] == 0.0


def test_assumed_never_exceeds_the_balance_after_withdrawals(db_session):
    """Draw the bucket most of the way down and the assumed part cannot exceed what is left, or the
    UI would report more unverified money than the fund claims to hold at all."""
    from api.sinking import compute_sinking
    h = _household(db_session)
    db_session.add(_fund(h.id, withdrawals=[{"amount": 30_000.0, "date": "2026-06-01", "note": "trip"}]))
    db_session.commit()

    fund = compute_sinking(db_session, h.id)["funds"][0]
    assert fund["assumed_inr"] <= max(0.0, fund["balance"])


def test_the_overspend_recovery_offer_carries_how_much_is_assumed(db_session):
    """The offer stays — the household knows its own transfers — but it may not be presented as
    certain cash without saying how much of it has never been observed."""
    from services.recovery import recovery_plan
    h = _household(db_session)
    db_session.add(_fund(h.id))
    db_session.commit()

    plan = recovery_plan(db_session, h.id, gap=10_000.0)
    sinking = [o for o in plan.get("options", []) if o.get("kind") == "sinking"]
    # Asserted, not skipped. An early return here would let the test go quiet the day the offer stops
    # being generated, and a test that stops testing is worse than one that fails.
    assert sinking, "a funded bucket must still be offered against a 10,000 gap"
    assert sinking[0]["available"] == 35_000.0          # 7 months x 5,000, none of it observed
    assert sinking[0]["assumed_inr"] == 35_000.0, "the whole offer is unverified and must say so"
