"""A dry run must write nothing. This is the test that should have existed before it was trusted.

`dry_run=True` was built as the safety mechanism for statement import and then used to preview 27
files against a live database. It wrote 55 rows. The claim afterwards was that ingest_statement
commits earlier in the flow than the rollback branch. That was never verified either way, so this
settles it by counting rows across a real call.
"""


import pytest
from sqlalchemy import func

from models.tenant import Household
from models.transaction import Transaction


class _Row:
    """What a parser yields."""
    def __init__(self, d, amt, desc):
        self.date, self.amount, self.description = d, amt, desc
        self.direction, self.bank = "debit", "ICICI"
        self.card_last4, self.stmt_month = "9911", d[:7]
        self.card_type = "Credit Card"


@pytest.fixture
def _hh(db_session):
    # conftest creates a subset of tables; ingest reads these two directly.
    from models.transaction import DeletedFingerprint, Statement
    for m in (DeletedFingerprint, Statement):
        m.__table__.create(db_session.get_bind(), checkfirst=True)
    h = Household(name="A"); db_session.add(h); db_session.commit()
    return h


def _rows():
    return [_Row("2026-07-04", 100.0, "COFFEE ONE"),
            _Row("2026-07-05", 250.0, "COFFEE TWO"),
            _Row("2026-07-06", 999.0, "SOMETHING ELSE")]


def _patch_parse(monkeypatch, rows):
    """Drive the real ingest_statement, stubbing only the file-format layer."""
    import services.ingest as ing
    monkeypatch.setattr(ing, "detect_statement", lambda *a, **k: None)
    monkeypatch.setattr(ing, "select_parser", lambda *a, **k: ((lambda *_a, **_k: rows), None))
    monkeypatch.setattr(ing, "find_password", lambda *a, **k: "")


def test_a_dry_run_leaves_the_row_count_untouched(db_session, monkeypatch, _hh):
    from services.ingest import ingest_statement

    _patch_parse(monkeypatch, _rows())
    before = db_session.query(func.count(Transaction.id)).scalar()
    r = ingest_statement(db_session, "x.pdf", b"%PDF-1.4", bank="ICICI",
                         card_type="Credit Card", card_last4="9911",
                         household_id=_hh.id, dry_run=True)
    after = db_session.query(func.count(Transaction.id)).scalar()
    assert after == before, f"dry run wrote {after - before} rows"
    assert r["dry_run"] is True
    assert r["would_insert"] == 3
    assert r["transactions_new"] == 0


def test_a_real_run_does_write(db_session, monkeypatch, _hh):
    """The control. If this also wrote nothing the test above would prove nothing."""
    from services.ingest import ingest_statement

    _patch_parse(monkeypatch, _rows())
    before = db_session.query(func.count(Transaction.id)).scalar()
    r = ingest_statement(db_session, "x.pdf", b"%PDF-1.4", bank="ICICI",
                         card_type="Credit Card", card_last4="9911",
                         household_id=_hh.id, dry_run=False, guard=False)
    after = db_session.query(func.count(Transaction.id)).scalar()
    assert after - before == 3
    assert r["transactions_new"] == 3


def test_a_dry_run_leaves_no_statement_row_either(db_session, monkeypatch, _hh):
    from models.transaction import Statement
    from services.ingest import ingest_statement

    _patch_parse(monkeypatch, _rows())
    before = db_session.query(func.count(Statement.id)).scalar()
    ingest_statement(db_session, "x.pdf", b"%PDF-1.4", bank="ICICI", card_type="Credit Card",
                     card_last4="9911", household_id=_hh.id, dry_run=True)
    assert db_session.query(func.count(Statement.id)).scalar() == before


# --- the mid-cycle export must not survive its billed statement ---------------

def _billed_rows():
    """The SAME three debits as _rows(), described the way the billed statement writes them.

    This is the whole difficulty: the bank's mid-cycle export says "BDSKAMAZONPAYIN" and the billed
    statement says "10000000044 AMAZON PAY IN GROCERY BANG" for one debit. The descriptions never
    match, so fingerprints differ and dedup cannot pair them. Only amount+date can.
    """
    return [_Row("2026-07-04", 100.0, "10000000011 COFFEE ONE BANGALORE IN"),
            _Row("2026-07-05", 250.0, "10000000022 COFFEE TWO BENGALURU IN"),
            _Row("2026-07-06", 999.0, "10000000033 SOMETHING ELSE URB IN")]


def _live_spend(db, hid):
    return (db.query(func.coalesce(func.sum(Transaction.amount), 0.0))
            .filter(Transaction.household_id == hid, Transaction.archived.is_(False),
                    Transaction.dup_of_id.is_(None)).scalar() or 0.0)


def test_the_billed_statement_supersedes_the_mid_cycle_export(db_session, monkeypatch, _hh):
    """The cycle after the fix: import the mid-cycle export, then the real statement, and the
    same three debits must be counted ONCE.

    Before the fix both files imported as authoritative statement rows. Supersede skipped them (it
    only looked at sms/gmail) and dedup refused them (N statement lines = N real debits), so both
    copies stayed live and the spend was counted twice.
    """
    from services.ingest import ingest_statement

    _patch_parse(monkeypatch, _rows())
    ingest_statement(db_session, "CCStatement_Current24-07-2026.xls", b"x", bank="ICICI",
                     card_type="Credit Card", card_last4="9911",
                     household_id=_hh.id, dry_run=False, guard=False)
    after_export = _live_spend(db_session, _hh.id)
    assert after_export == pytest.approx(1349.0), "the export's own rows should be live"

    _patch_parse(monkeypatch, _billed_rows())
    ingest_statement(db_session, "4111XXXXXXXX9911_Retail_NORM.pdf", b"y", bank="ICICI",
                     card_type="Credit Card", card_last4="9911",
                     household_id=_hh.id, dry_run=False, guard=False)

    live = _live_spend(db_session, _hh.id)
    assert live == pytest.approx(1349.0), (
        f"spend is {live}, not 1349 — the mid-cycle copies were not superseded and are double-counted")

    archived = (db_session.query(func.count(Transaction.id))
                .filter(Transaction.household_id == _hh.id, Transaction.archived.is_(True)).scalar())
    assert archived == 3, f"expected the 3 mid-cycle rows archived, got {archived}"


def test_two_billed_statements_are_never_collapsed(db_session, monkeypatch, _hh):
    """The guard on the fix. A real statement must stay authoritative: if two BILLED statements
    both list a debit, that is two real debits and superseding one would delete real spend."""
    from services.ingest import ingest_statement

    _patch_parse(monkeypatch, _rows())
    ingest_statement(db_session, "statement_july.pdf", b"x", bank="ICICI", card_type="Credit Card",
                     card_last4="9911", household_id=_hh.id, dry_run=False, guard=False)
    _patch_parse(monkeypatch, _billed_rows())
    ingest_statement(db_session, "statement_august.pdf", b"y", bank="ICICI", card_type="Credit Card",
                     card_last4="9911", household_id=_hh.id, dry_run=False, guard=False)

    archived = (db_session.query(func.count(Transaction.id))
                .filter(Transaction.household_id == _hh.id, Transaction.archived.is_(True)).scalar())
    assert archived == 0, "a billed statement was superseded by another billed statement"
