"""An automated import must refuse a statement for an account the household does not hold.

An ICICI SAVINGS statement for XX1234 arrived on the ICICI card sender and was handed to the card
parser. Bank statements carry a running-balance column that card statements do not, so every column
shifted: the balance was read as the amount. It booked Rs 25,000 of spend against about Rs 1,700 of
real spending, and flipped a closed cycle from under budget to breached.

Scoped to automated imports on purpose. A person uploading a file has decided it belongs here;
refusing that would make it impossible to add a card you have not registered yet.
"""

import pytest
from sqlalchemy import func

from models.tenant import Household
from models.transaction import Transaction


class _Row:
    def __init__(self, d, amt, desc):
        self.date, self.amount, self.description = d, amt, desc
        self.direction, self.bank = "debit", "ICICI"
        self.card_last4, self.stmt_month = "6611", d[:7]
        self.card_type = "Credit Card"


@pytest.fixture
def _hh(db_session):
    from models.transaction import DeletedFingerprint, Statement
    for m in (DeletedFingerprint, Statement):
        m.__table__.create(db_session.get_bind(), checkfirst=True)
    h = Household(name="A"); db_session.add(h); db_session.commit()
    return h


def _patch(monkeypatch):
    import services.ingest as ing
    rows = [_Row("2026-07-13", 7000.50, "749.00"), _Row("2026-07-21", 6000.50, "950.00")]
    monkeypatch.setattr(ing, "detect_statement", lambda *a, **k: None)
    monkeypatch.setattr(ing, "select_parser", lambda *a, **k: ((lambda *_a, **_k: rows), None))
    monkeypatch.setattr(ing, "find_password", lambda *a, **k: "")


def test_automated_import_refuses_an_unknown_account(db_session, monkeypatch, _hh):
    from services.ingest import ingest_statement
    _patch(monkeypatch)
    before = db_session.query(func.count(Transaction.id)).scalar()
    r = ingest_statement(db_session, "Statement_2026MTH07_524574055.pdf", b"x", bank="ICICI",
                         card_type="Credit Card", card_last4="6611", household_id=_hh.id,
                         dry_run=False, guard=False, require_known_account=True)
    assert r["ok"] is False
    assert "6611" in r["error"]
    assert db_session.query(func.count(Transaction.id)).scalar() == before, "it wrote rows anyway"


def test_a_human_upload_is_still_allowed(db_session, monkeypatch, _hh):
    """The control. Without this the guard would block onboarding a new card."""
    from services.ingest import ingest_statement
    _patch(monkeypatch)
    r = ingest_statement(db_session, "my_new_card.pdf", b"x", bank="ICICI",
                         card_type="Credit Card", card_last4="6611", household_id=_hh.id,
                         dry_run=False, guard=False)
    assert r.get("ok") is not False, f"a manual upload was refused: {r.get('error')}"
