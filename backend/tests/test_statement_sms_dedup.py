"""A statement row and the SMS alert for the same purchase must merge, not both survive.

Written expecting these to fail, because a statement import added 43 rows to a live household and
22 duplicated transactions the SMS capture already held, inflating a cycle by roughly a fifth. Dedup looked like the culprit.

It is not. These pass: the engine clusters "AMAZON PAY" against "10000000011 AMAZON PAY IN E CO",
rates it high confidence, and merges it. The duplicates on live survived for a reason still not
established, and blaming dedup without checking was the same error as trusting the row count that
started this.
"""

from datetime import date, timedelta

import pytest  # noqa: F401

from models.tenant import Household
from models.transaction import Transaction


def _txn(hid, when, amount, desc, source, bank="ICICI", last4="9911"):
    return Transaction(
        household_id=hid, date=when.isoformat(), description=desc, amount=amount,
        direction="debit", source=source, bank=bank, card_type="Credit Card",
        card_last4=last4, stmt_month=when.strftime("%Y-%m"),
        fingerprint=f"{source}-{desc}-{amount}-{when}",
    )


@pytest.fixture
def _hh(db_session):
    h = Household(name="A"); db_session.add(h); db_session.commit()
    return h


def test_a_statement_row_merges_with_the_sms_for_the_same_purchase(db_session, _hh):
    from services.dedup import auto_merge

    d = date(2026, 7, 4)
    db_session.add(_txn(_hh.id, d, 2.0, "RAZ*Example Assist Services", "sms"))
    db_session.add(_txn(_hh.id, d, 2.0, "10000000055 RAZ*Example Assist Se", "statement"))
    db_session.commit()

    auto_merge(db_session, household_id=_hh.id)
    live = db_session.query(Transaction).filter(
        Transaction.household_id == _hh.id, Transaction.dup_of_id.is_(None)).count()
    assert live == 1, "the same purchase survived as two rows"


def test_same_amount_and_day_across_sources_is_treated_as_one(db_session, _hh):
    from services.dedup import auto_merge

    d = date(2026, 6, 12)
    db_session.add(_txn(_hh.id, d, 877.0, "AMAZON PAY", "sms"))
    db_session.add(_txn(_hh.id, d + timedelta(days=1), 877.0, "10000000011 AMAZON PAY IN E CO", "statement"))
    db_session.commit()

    auto_merge(db_session, household_id=_hh.id)
    assert db_session.query(Transaction).filter(
        Transaction.household_id == _hh.id, Transaction.dup_of_id.is_(None)).count() == 1


def test_two_genuinely_different_purchases_are_not_merged(db_session, _hh):
    """The guard on the guard: a fix that merges anything of equal value would silently delete real
    spending. Two identical coffees on the same day are two transactions."""
    from services.dedup import auto_merge

    d = date(2026, 7, 4)
    db_session.add(_txn(_hh.id, d, 250.0, "BLUE TOKAI MORNING", "statement"))
    db_session.add(_txn(_hh.id, d, 250.0, "BLUE TOKAI EVENING", "statement"))
    db_session.commit()

    auto_merge(db_session, household_id=_hh.id)
    assert db_session.query(Transaction).filter(
        Transaction.household_id == _hh.id, Transaction.dup_of_id.is_(None)).count() == 2
