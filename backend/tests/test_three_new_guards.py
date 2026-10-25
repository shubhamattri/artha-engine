"""The three guards added for the failures that were found by a person, not the system.

Each one is pinned in BOTH directions. A guard that never fires is decoration; a guard that fires on
healthy data gets learned as noise and then ignored, which is worse than not having it.
"""

from datetime import datetime, timedelta

import pytest

from models.tenant import Household
from models.transaction import Transaction
from services.consistency import (
    Report,
    check_no_card_purchase_typed_as_emi,
    check_sync_is_alive,
)


@pytest.fixture
def _hh(db_session):
    from models.sync_run import SyncRun
    SyncRun.__table__.create(db_session.get_bind(), checkfirst=True)
    h = Household(name="A"); db_session.add(h); db_session.commit()
    return h


def _run(db, hid, hours_ago, ok=True):
    from models.sync_run import SyncRun
    t = datetime.utcnow() - timedelta(hours=hours_ago)
    db.add(SyncRun(household_id=hid, started_at=t, finished_at=t, ok=ok)); db.commit()


def _emi(db, hid, amount, desc):
    db.add(Transaction(household_id=hid, fingerprint=f"e{amount}{desc}", date="2026-08-13",
                       description=desc, amount=amount, direction="debit", txn_type="EMI",
                       bank="ICICI", card_type="Credit Card", card_last4="9911",
                       stmt_month="2026-08"))
    db.commit()


# --- sync heartbeat ----------------------------------------------------------

def test_a_dead_sync_is_reported(db_session, _hh):
    """The exact situation tonight: purchases made, nothing captured, nothing said."""
    _run(db_session, _hh.id, hours_ago=9)
    r = Report(); check_sync_is_alive(db_session, _hh.id, r)
    assert r.errors, "a sync silent for 9 hours reported healthy"
    assert "nothing has been captured" in r.errors[0].detail


def test_a_live_sync_is_quiet(db_session, _hh):
    """The control. It runs every 10 min; alarming on a normal gap would train you to ignore it."""
    _run(db_session, _hh.id, hours_ago=0.2)
    r = Report(); check_sync_is_alive(db_session, _hh.id, r)
    assert not r.errors, f"fired on a healthy sync: {[f.detail for f in r.errors]}"


def test_a_failing_sync_does_not_count_as_alive(db_session, _hh):
    """A job that runs and fails every time must not look healthy just because it ran."""
    _run(db_session, _hh.id, hours_ago=0.1, ok=False)
    r = Report(); check_sync_is_alive(db_session, _hh.id, r)
    assert not any(f.severity == "ok" for f in r.findings), "a failing run was treated as alive"


# --- misfiled EMI ------------------------------------------------------------

def test_a_purchase_typed_as_emi_is_reported(db_session, _hh):
    """A card purchase that vanished from spend without leaving the ledger."""
    _emi(db_session, _hh.id, 4000.0, "AMAZON PAY")
    r = Report(); check_no_card_purchase_typed_as_emi(db_session, _hh.id, r)
    assert r.errors, "a card purchase typed EMI passed"
    assert "excluded from spend" in r.errors[0].detail


def test_an_uninformative_description_is_not_flagged(db_session, _hh):
    """The first version flagged legitimate rows (builder instalments
    and auto-debits described only as 'ICICIBANK'). Uninformative is not the same as misfiled, and an
    alert that fires on real data is one you learn to ignore."""
    _emi(db_session, _hh.id, 3000.0, "100200300400/CCAEXAMPLEBUILDERSC")
    _emi(db_session, _hh.id, 2345.0, "ICICIBANK")
    _emi(db_session, _hh.id, 1000.0, "HDFC Bank Online Banking")
    r = Report(); check_no_card_purchase_typed_as_emi(db_session, _hh.id, r)
    assert not r.errors, f"fired on uninformative but legitimate rows: {[f.detail for f in r.errors]}"


def test_a_real_emi_is_left_alone(db_session, _hh):
    """The control: genuine EMIs name their loan and must not be flagged every single run."""
    _emi(db_session, _hh.id, 25000.0, "EMI of Rs 25000 for Example Bank Home Loan XX1122")
    _emi(db_session, _hh.id, 8000.0, "EMI for your Kotak Loan A/c No. SPLN100")
    r = Report(); check_no_card_purchase_typed_as_emi(db_session, _hh.id, r)
    assert not r.errors, f"fired on real EMIs: {[f.detail for f in r.errors]}"
