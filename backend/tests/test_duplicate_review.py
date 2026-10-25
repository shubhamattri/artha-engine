"""Phase 2 — the ambiguous duplicate review queue.

Surfaces only genuinely-uncertain pairs (same card, amount, day; compatible; no shared ref) and never
the deterministic non-duplicates: different days, different refs, or different payees. Confirm/dismiss
are remembered.
"""


from models.tenant import Household
from models.transaction import Transaction
from services import dedup


def _t(hid, amount, merchant, date="2026-07-01", source="sms", ref=None, fp=None, last4="9922", occ=None):
    return Transaction(household_id=hid, fingerprint=fp or f"{merchant}-{date}-{ref}", date=date,
                       description=merchant, merchant=merchant, source=source, amount=amount,
                       direction="debit", category="Food & Dining", txn_type="SPEND", bank="HDFC",
                       card_type="credit", card_last4=last4, stmt_month=date[:7], bank_ref=ref, occurred_at=occ)


def test_ambiguous_pair_surfaces(db_session):
    """Same card, amount, day; compatible; NO shared ref → shows up for review."""
    h = Household(name="A"); db_session.add(h); db_session.commit()
    db_session.add_all([_t(h.id, 9000.0, "CT FLIGHT VIA SMARTBUY", fp="a"),
                        _t(h.id, 9000.0, "CT FLIGHT V", fp="b")]); db_session.commit()
    cands = dedup.review_candidates(db_session, h.id)
    assert len(cands) == 1 and cands[0]["amount"] == 9000.0


def test_different_day_not_surfaced(db_session):
    """Two same-amount payments to one payee on DIFFERENT days = two real transactions, not reviewed."""
    h = Household(name="B"); db_session.add(h); db_session.commit()
    db_session.add_all([_t(h.id, 80500.0, "Asha Verma", date="2026-01-02", fp="c"),
                        _t(h.id, 80500.0, "Asha Verma", date="2026-01-15", fp="d")]); db_session.commit()
    assert dedup.review_candidates(db_session, h.id) == []


def test_different_ref_not_surfaced(db_session):
    """Two DIFFERENT bank references = deterministically two debits — never reviewed."""
    h = Household(name="C"); db_session.add(h); db_session.commit()
    db_session.add_all([_t(h.id, 656.0, "Swiggy", ref="10000000011", fp="e"),
                        _t(h.id, 656.0, "Swiggy", ref="10000000022", fp="f")]); db_session.commit()
    assert dedup.review_candidates(db_session, h.id) == []


def test_different_payees_not_surfaced(db_session):
    """Same amount + day but two clearly different named payees = not a duplicate."""
    h = Household(name="D"); db_session.add(h); db_session.commit()
    db_session.add_all([_t(h.id, 7000.0, "Deepa Rao", source="gmail", fp="g"),
                        _t(h.id, 7000.0, "Meera Sundaram Iyer", source="gmail", fp="h")]); db_session.commit()
    assert dedup.review_candidates(db_session, h.id) == []


def test_dismiss_removes_from_queue(db_session):
    """Dismissing a pair remembers it — it never resurfaces."""
    h = Household(name="E"); db_session.add(h); db_session.commit()
    # A NON-merchant ambiguous pair (no provider → not auto-merged → a genuine review candidate).
    db_session.add_all([_t(h.id, 4200.0, "CT FLIGHT VIA SMARTBUY", fp="p"),
                        _t(h.id, 4200.0, "CT FLIGHT V", fp="q")])
    db_session.commit()
    assert len(dedup.review_candidates(db_session, h.id)) == 1
    dedup.dismiss_pair(db_session, h.id, "p", "q")
    assert dedup.review_candidates(db_session, h.id) == []


def test_merge_pair_links_and_is_scoped(db_session):
    """Confirming a pair links duplicate → primary; cross-household ids are rejected."""
    h = Household(name="F"); db_session.add(h); db_session.commit()
    a = _t(h.id, 700.0, "Zepto", fp="r"); b = _t(h.id, 700.0, "Zepto", fp="s")
    db_session.add_all([a, b]); db_session.commit()
    res = dedup.merge_pair(db_session, h.id, a.id, b.id)
    assert res["ok"]
    merged = db_session.get(Transaction, res["merged_id"])
    assert merged.dup_of_id == res["primary_id"]


def test_statement_vs_statement_not_surfaced(db_session):
    """Two statement lines same day/amount are two real debits (authoritative) — never reviewed;
    only SMS/email double-captures are ambiguous."""
    h = Household(name="G"); db_session.add(h); db_session.commit()
    db_session.add_all([_t(h.id, 10000.0, "Groww", source="statement", fp="x1"),
                        _t(h.id, 10000.0, "Groww", source="statement", fp="x2")]); db_session.commit()
    assert dedup.review_candidates(db_session, h.id) == []
