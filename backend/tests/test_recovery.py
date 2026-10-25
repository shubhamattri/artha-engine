"""Over-budget recovery advisor — must be CASHFLOW-ACCURATE, not advice-shaped.

The core rule the first cut got wrong: money already spent on overspent categories is GONE — it can't
cover today's shortfall. Only real cash levers (pausable SIP, sinking-fund balances) cover a realized
gap, and we take ONLY what's needed.
"""

from datetime import date

from models.recurring import RecurringInvestment
from models.sinking import SinkingFund
from models.tenant import Household
from services import recovery


def _sip(hid: int, amount: float) -> RecurringInvestment:
    return RecurringInvestment(household_id=hid, name="Index SIP", kind="sip",
                               opening_balance=0.0, as_of_date=date(2026, 1, 1),
                               monthly_contribution=amount, from_takehome=True, active=True,
                               compounding="monthly", annual_rate_pct=12.0)


def test_gap_covered_from_pausable_sip_taking_only_whats_needed(db_session):
    h = Household(name="HR1"); db_session.add(h); db_session.commit()
    db_session.add(_sip(h.id, 50000.0)); db_session.commit()

    plan = recovery.recovery_plan(db_session, h.id, 9000.0)
    assert plan["covered"] is True and plan["shortfall"] == 0.0
    assert len(plan["options"]) == 1
    o = plan["options"][0]
    assert o["kind"] == "pause_sip"
    assert o["amount"] == 9000.0 and o["available"] == 50000.0   # only what's needed, not the whole SIP
    assert o["action"]["type"] == "skip_sip" and len(o["action"]["sips"]) == 1    # one-tap target carried


def test_past_overspend_is_never_counted_as_cash(db_session):
    """No cash levers → the gap is NOT magically covered by 'reining in' already-spent categories."""
    h = Household(name="HR2"); db_session.add(h); db_session.commit()
    plan = recovery.recovery_plan(db_session, h.id, 8000.0)
    assert plan["covered"] is False
    assert all(o["kind"] != "trim" for o in plan["options"])       # trim is never a cash source
    assert plan["options"] and plan["options"][-1]["kind"] == "borrow"
    assert plan["options"][-1]["amount"] == 8000.0                 # full gap → last-resort bridge


def test_sip_then_sinking_then_bridge_in_order(db_session):
    h = Household(name="HR3"); db_session.add(h); db_session.commit()
    db_session.add_all([
        _sip(h.id, 5000.0),
        SinkingFund(household_id=h.id, name="Car service", opening_balance=4000.0,
                    as_of_date=date(2026, 1, 1), monthly_contribution=0.0, active=True),
    ]); db_session.commit()
    plan = recovery.recovery_plan(db_session, h.id, 12000.0)     # 5k SIP + 4k sinking = 9k, 3k short
    kinds = [o["kind"] for o in plan["options"]]
    assert kinds == ["pause_sip", "sinking", "borrow"]
    assert plan["options"][0]["amount"] == 5000.0
    assert plan["options"][1]["amount"] == 4000.0
    assert plan["options"][2]["amount"] == 3000.0 and plan["covered"] is False


def test_zero_gap_is_a_noop(db_session):
    h = Household(name="HR4"); db_session.add(h); db_session.commit()
    plan = recovery.recovery_plan(db_session, h.id, 0.0)
    assert plan["covered"] is True and plan["options"] == []


def test_partial_sip_skip_reduces_takehome_and_invested_only_by_skip():
    """A partial skip frees EXACTLY the skipped amount this month (take-home) and reduces invested by the
    money that never went in — not the whole SIP. This is what makes 'skip ₹X' honest, not pause-all."""
    from datetime import date
    from api.recurring import _view
    from models.recurring import RecurringInvestment
    common = dict(household_id=1, name="SIP", kind="rd", opening_balance=0.0, as_of_date=date(2026, 1, 1),
                  monthly_contribution=50000.0, employer_contribution=0.0, annual_rate_pct=7.0,
                  compounding="quarterly", active=True, from_takehome=True, contributions=[])
    full = _view(RecurringInvestment(**common, skips=[]), date.today())
    skipped = _view(RecurringInvestment(**common, skips=[{"month": date.today().strftime("%Y-%m"), "amount": 9000.0}]), date.today())
    assert skipped["takehome_outflow"] == 41000.0                       # 50000 − 9000, only this month
    assert round(full["total_contributed"] - skipped["total_contributed"]) == 9000   # exactly the skip
