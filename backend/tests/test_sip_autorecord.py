"""A SIP keeps recording itself after the statement that seeded it.

Once a fund's ledger is declared complete, nothing is invented on top of it — which is the fix that
made a real XIRR possible, and also means next month's instalment is simply missing until another
statement arrives. This fills that gap from what is already known: the standing instruction, and the
NAV AMFI published on the day the money went in.

The tests that matter here are the ones about NOT writing: never on a fund whose instruction is still
being synthesised, never for a month that has not happened, never twice, and never with a made-up
price.
"""

from datetime import date, timedelta

import pytest
from models.recurring import RecurringInvestment
from models.tenant import Household
from services import mf_nav, sip_autorecord


@pytest.fixture()
def nav_at_100(monkeypatch):
    """Every past date prices at 100, so units are trivially checkable and no test hits the feed."""
    monkeypatch.setattr(mf_nav, "fetch_nav_on", lambda code, on: (100.0, on.isoformat()))


def _household(db_session) -> Household:
    h = Household(name="AUTO")
    db_session.add(h)
    db_session.commit()
    return h


def _fund(db_session, hid, *, authoritative=True, active=True, entries=None, monthly=10_000.0):
    inv = RecurringInvestment(
        household_id=hid, name="Auto Flexi Cap", kind="sip", holder="Household",
        opening_balance=0.0, as_of_date=date.today() - timedelta(days=400),
        monthly_contribution=monthly, employer_contribution=0.0, annual_rate_pct=12.0,
        compounding="monthly", active=active, from_takehome=True,
        ledger_is_authoritative=authoritative,
        contributions=entries if entries is not None else [],
        market={"scheme_code": "122639", "units": 0.0},
    )
    db_session.add(inv)
    db_session.commit()
    return inv


def _entry(d: date, amount=10_000.0):
    return {"date": d.isoformat(), "amount": amount, "units": 100.0, "nav": 100.0}


def test_it_records_the_months_that_have_passed_since_the_last_entry(db_session, nav_at_100):
    h = _household(db_session)
    inv = _fund(db_session, h.id, entries=[_entry(date.today().replace(day=5) - timedelta(days=95))])
    out = sip_autorecord.record_due(db_session, h.id)
    assert out["instalments"] >= 2
    db_session.refresh(inv)
    autos = [c for c in inv.contributions if c.get("auto")]
    assert all(c["units"] == round(c["amount"] / c["nav"], 4) for c in autos)


def test_it_never_touches_a_fund_whose_instruction_is_still_synthesised(db_session, nav_at_100):
    """The whole trap, third time. A non-authoritative fund already has its monthly flow generated at
    read time; writing a real entry as well counts the same money twice."""
    h = _household(db_session)
    inv = _fund(db_session, h.id, authoritative=False,
                entries=[_entry(date.today().replace(day=5) - timedelta(days=95))])
    assert sip_autorecord.record_due(db_session, h.id)["instalments"] == 0
    db_session.refresh(inv)
    assert not any(c.get("auto") for c in inv.contributions)


def test_a_stopped_sip_records_nothing(db_session, nav_at_100):
    """SIP off but units still held is a real state two of this household's funds are in. Money is
    not going in, so no instalment may appear."""
    h = _household(db_session)
    _fund(db_session, h.id, active=False,
          entries=[_entry(date.today().replace(day=5) - timedelta(days=95))])
    assert sip_autorecord.record_due(db_session, h.id)["instalments"] == 0


def test_running_it_twice_adds_nothing_the_second_time(db_session, nav_at_100):
    """The sync calls this every ten minutes."""
    h = _household(db_session)
    _fund(db_session, h.id, entries=[_entry(date.today().replace(day=5) - timedelta(days=95))])
    first = sip_autorecord.record_due(db_session, h.id)["instalments"]
    assert first > 0
    assert sip_autorecord.record_due(db_session, h.id)["instalments"] == 0


def test_it_does_not_record_an_instalment_that_has_not_happened_yet(db_session, nav_at_100):
    """A due date later this month is still money sitting in the bank. Pinned on a FIXED today, so
    the assertion means the same thing whatever day this suite is run — an earlier version of this
    test depended on the real date and pinned nothing at all: a mutation that recorded future
    instalments passed it."""
    h = _household(db_session)
    inv = _fund(db_session, h.id, entries=[_entry(date(2026, 4, 20))])
    # 20 May and 20 Jun have happened by 10 July; 20 Jul has not.
    sip_autorecord.record_due(db_session, h.id, today=date(2026, 7, 10))
    db_session.refresh(inv)
    dates = sorted(c["date"] for c in inv.contributions)
    assert dates == ["2026-04-20", "2026-05-20", "2026-06-20"]


def test_a_month_with_no_published_nav_is_left_as_a_gap(db_session, monkeypatch):
    """A missing price is a wait, not a licence to invent one. The gap fills itself on a later run
    once the feed catches up."""
    monkeypatch.setattr(mf_nav, "fetch_nav_on", lambda code, on: None)
    h = _household(db_session)
    inv = _fund(db_session, h.id, entries=[_entry(date(2026, 4, 20))])
    out = sip_autorecord.record_due(db_session, h.id, today=date(2026, 7, 10))
    assert out["instalments"] == 0
    assert out["unpriced"] == 2   # May and June; 20 Jul has not happened by 10 Jul
    db_session.refresh(inv)
    assert len(inv.contributions) == 1


def test_a_fund_with_no_recorded_history_is_skipped_not_guessed(db_session, nav_at_100):
    """Nothing stores the mandate day, so it is derived from what actually landed. With no record
    there is nothing to derive from, and a guessed day would write instalments on the wrong dates."""
    h = _household(db_session)
    _fund(db_session, h.id, entries=[])
    assert sip_autorecord.record_due(db_session, h.id)["instalments"] == 0
