"""Two different files covering the same month must not share one Statement record.

The lookup matched on (bank, card_type, stmt_month), so a mid-cycle export and the billed statement
for the same month wrote their rows into the SAME record. Origin then became unrecoverable: on card
9911, one row from 23 Jun and another from 11 Aug both carried statement_id=10. Any cleanup keyed on
statement_id would have flagged the BILLED rows as provisional and let the next import archive the
authoritative copy -- inverting a double-count into data loss.
"""

import pytest
from sqlalchemy import func

from models.tenant import Household
from models.transaction import Statement


class _Row:
    def __init__(self, d, amt, desc):
        self.date, self.amount, self.description = d, amt, desc
        self.direction, self.bank = "debit", "ICICI"
        self.card_last4, self.stmt_month = "9911", "2026-06"
        self.card_type = "Credit Card"


@pytest.fixture
def _hh(db_session):
    from models.transaction import DeletedFingerprint
    for m in (DeletedFingerprint, Statement):
        m.__table__.create(db_session.get_bind(), checkfirst=True)
    h = Household(name="A"); db_session.add(h); db_session.commit()
    return h


def _patch(monkeypatch, rows):
    import services.ingest as ing
    monkeypatch.setattr(ing, "detect_statement", lambda *a, **k: None)
    monkeypatch.setattr(ing, "select_parser", lambda *a, **k: ((lambda *_a, **_k: rows), None))
    monkeypatch.setattr(ing, "find_password", lambda *a, **k: "")


def test_two_files_for_one_month_get_two_records(db_session, monkeypatch, _hh):
    from services.ingest import ingest_statement
    _patch(monkeypatch, [_Row("2026-06-14", 100.0, "A")])
    ingest_statement(db_session, "CCStatement_Current24-06-2026.xls", b"x", bank="ICICI",
                     card_type="Credit Card", card_last4="9911", household_id=_hh.id,
                     dry_run=False, guard=False)
    _patch(monkeypatch, [_Row("2026-06-14", 100.0, "10000000044 A BANGALORE")])
    ingest_statement(db_session, "4111XXXXXXXX9911_Retail_NORM.pdf", b"y", bank="ICICI",
                     card_type="Credit Card", card_last4="9911", household_id=_hh.id,
                     dry_run=False, guard=False)
    n = db_session.query(func.count(Statement.id)).filter(Statement.stmt_month == "2026-06").scalar()
    assert n == 2, f"two files for one month collapsed into {n} record(s) — origin is unrecoverable"


def test_reimporting_the_same_file_reuses_its_record(db_session, monkeypatch, _hh):
    """The control: idempotency must survive. Same file twice is still one record."""
    from services.ingest import ingest_statement
    _patch(monkeypatch, [_Row("2026-06-14", 100.0, "A")])
    for _ in range(2):
        ingest_statement(db_session, "same_file.pdf", b"x", bank="ICICI", card_type="Credit Card",
                         card_last4="9911", household_id=_hh.id, dry_run=False, guard=False)
    n = db_session.query(func.count(Statement.id)).filter(Statement.stmt_month == "2026-06").scalar()
    assert n == 1, f"re-importing one file created {n} records"
