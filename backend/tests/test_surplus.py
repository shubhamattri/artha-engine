"""A cycle's leftover must be traceable, and must never be counted as new money.

"We saved money last cycle — what happened to it?" had no answer: the leftover was computed, shown
once, and then existed nowhere. The danger in fixing that is recording surplus as an ASSET. The
rupees are already in a bank account whose balance is already in net worth, so that would count the
same money twice — the exact shape of the errors that made this app untrustworthy.
"""

import pytest

from models.tenant import Household
from services import surplus


@pytest.fixture
def _hh(db_session, monkeypatch):
    from models.surplus import SurplusAllocation, SurplusDraw
    for m in (SurplusAllocation, SurplusDraw):
        m.__table__.create(db_session.get_bind(), checkfirst=True)
    h = Household(name="A"); db_session.add(h); db_session.commit()
    # Budget and cash-flow are separate concerns with their own tests. Pin both so these assert on
    # the SURPLUS rules: a pool of 1,50,000 with 1,00,000 spent leaves 50,000 saved.
    monkeypatch.setattr("api.budget.compute_budget", lambda **k: {"net_spent_inr": 100000.0})
    monkeypatch.setattr("api.cashflow.compute_cashflow", lambda *a, **k: {"leftover": 150000.0})
    return h


def test_a_cycle_leftover_can_be_earmarked_and_traced(db_session, _hh):
    a = surplus.allocate(db_session, _hh.id, "2026-08-12", 30000.0, "prepayment", "HDFC PL")
    assert surplus.remaining(db_session, a) == 30000.0
    surplus.draw(db_session, a.id, 12000.0, on="2026-08-20", loan_id=None, note="part payment")
    assert surplus.remaining(db_session, a) == 18000.0
    s = surplus.summary(db_session, _hh.id)
    assert s["total_remaining"] == 18000.0
    assert s["allocations"][0]["draws"][0]["amount"] == 12000.0, "the draw is not traceable"


def test_it_refuses_to_earmark_money_the_cycle_did_not_leave(db_session, _hh):
    """Without this the claimed total drifts above the money that exists, silently."""
    surplus.allocate(db_session, _hh.id, "2026-08-12", 45000.0, "buffer")
    with pytest.raises(ValueError, match="left to earmark"):
        surplus.allocate(db_session, _hh.id, "2026-08-12", 10000.0, "invest")


def test_it_refuses_to_overdraw(db_session, _hh):
    """An allocation that can go negative stops answering 'what is left'."""
    a = surplus.allocate(db_session, _hh.id, "2026-08-12", 5000.0, "invest")
    with pytest.raises(ValueError, match="cannot draw"):
        surplus.draw(db_session, a.id, 6000.0)


def test_surplus_is_never_added_to_net_worth(db_session, _hh):
    """The whole trap. These rupees are already in a bank balance that net worth counts."""
    from api.networth import networth_summary
    before = networth_summary(db=db_session, household=_hh)
    surplus.allocate(db_session, _hh.id, "2026-08-12", 40000.0, "prepayment")
    after = networth_summary(db=db_session, household=_hh)
    b = before.get("net_worth_inr", before.get("net_worth"))
    a = after.get("net_worth_inr", after.get("net_worth"))
    assert a == b, f"earmarking surplus moved net worth {b} -> {a} — the same money is counted twice"
