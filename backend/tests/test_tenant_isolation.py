"""Tenant isolation — the security-critical guarantee that a household can never read another's data.

This is the definition of "done" for BUILD-LOG M1 Slice 1: GET /transactions must return ONLY the
acting household's rows. Written to FAIL before the endpoint is scoped, and PASS after.
"""

import datetime
from datetime import date

from models.asset import Asset
from models.household import Budget, CashflowLine, Loan
from models.insurance import InsurancePolicy
from models.recurring import RecurringInvestment
from models.sinking import SinkingFund
from models.tenant import Household
from models.transaction import Transaction
from models.user import User


def _txn(household_id: int, fp: str, amount: float) -> Transaction:
    return Transaction(
        household_id=household_id, fingerprint=fp, date="2026-06-01", description="X",
        source="manual", amount=amount, direction="debit", bank="HDFC", card_type="x",
        card_last4="0000", stmt_month="2026-06",
    )


def test_transactions_are_scoped_to_the_current_household(db_session, client_for):
    a = Household(name="A")
    b = Household(name="B")
    db_session.add_all([a, b])
    db_session.commit()

    # two rows for A, one for B
    db_session.add_all([
        _txn(a.id, "a1", 100.0),
        _txn(a.id, "a2", 200.0),
        _txn(b.id, "b1", 300.0),
    ])
    db_session.commit()

    # acting as household B, we must see ONLY B's single transaction — never A's
    resp = client_for(b).get("/api/transactions")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1, f"leaked rows from another household: {data}"
    assert {t["amount"] for t in data["transactions"]} == {300.0}


def test_edits_are_recorded_in_the_audit_log(db_session, client_for):
    """Every user edit must append an immutable event with old→new, so history/undo/learning have a
    substrate. Editing amount + category should write two events for that transaction."""
    from models.audit import TransactionEvent

    a = Household(name="A")
    db_session.add(a)
    db_session.commit()
    t = _txn(a.id, "e1", 100.0)
    db_session.add(t)
    db_session.commit()

    resp = client_for(a).patch(f"/api/transactions/{t.id}", json={"amount": 250.0, "category": "Groceries"})
    assert resp.status_code == 200
    events = db_session.query(TransactionEvent).filter(TransactionEvent.txn_id == t.id).all()
    by_field = {e.field: e for e in events}
    assert "amount" in by_field and "category" in by_field
    assert by_field["amount"].old_value == "100.0" and by_field["amount"].new_value == "250.0"
    assert by_field["amount"].actor == "user" and by_field["category"].new_value == "Groceries"


def _two_households_with_rows(db_session):
    a, b = Household(name="A"), Household(name="B")
    db_session.add_all([a, b])
    db_session.commit()
    ta = _txn(a.id, "a1", 100.0)
    db_session.add_all([ta, _txn(b.id, "b1", 300.0)])
    db_session.commit()
    return a, b, ta


def test_cannot_edit_another_households_transaction(db_session, client_for):
    a, b, ta = _two_households_with_rows(db_session)
    # acting as B, try to edit A's row by id → must 404, and A's row must be untouched
    resp = client_for(b).patch(f"/api/transactions/{ta.id}", json={"amount": 99999.0})
    assert resp.status_code == 404
    db_session.expire_all()
    assert db_session.get(type(ta), ta.id).amount == 100.0


def test_cannot_delete_another_households_transaction(db_session, client_for):
    a, b, ta = _two_households_with_rows(db_session)
    # acting as B, try to delete A's row by id → must 404, and A's row must still exist & be active
    resp = client_for(b).delete(f"/api/transactions/{ta.id}")
    assert resp.status_code == 404
    db_session.expire_all()
    still = db_session.get(type(ta), ta.id)
    assert still is not None and still.archived is False


def test_facets_are_scoped_to_the_household(db_session, client_for):
    a, b = Household(name="A"), Household(name="B")
    db_session.add_all([a, b])
    db_session.commit()
    a1 = _txn(a.id, "a1", 100.0); a1.stmt_month = "2026-04"
    b1 = _txn(b.id, "b1", 300.0); b1.stmt_month = "2026-09"
    db_session.add_all([a1, b1])
    db_session.commit()

    facets = client_for(b).get("/api/transactions/facets").json()
    assert facets["months"] == ["2026-09"], f"facets leaked another household's months: {facets}"


def test_quick_add_stamps_the_current_household(db_session, client_for):
    a, b = Household(name="A"), Household(name="B")
    db_session.add_all([a, b])
    db_session.commit()

    # B logs a spend → the new row must belong to B, and must NOT appear in A's ledger
    resp = client_for(b).post("/api/transactions/quick", json={"amount": 250.0, "note": "Coffee"})
    assert resp.status_code == 200
    new_id = resp.json()["id"]
    row = db_session.get(Transaction, new_id)
    assert row.household_id == b.id

    assert client_for(a).get("/api/transactions").json()["total"] == 0
    assert client_for(b).get("/api/transactions").json()["total"] == 1


def test_household_is_resolved_from_the_user_not_the_first_household(db_session):
    """get_current_household must follow the logged-in user's household_id — not just return the
    first household. This is the actual multi-tenant unlock (Slice 4)."""
    from api.deps import get_current_household

    a, b = Household(name="A"), Household(name="B")
    db_session.add_all([a, b])
    db_session.commit()
    # a user that belongs to the SECOND household
    u = User(household_id=b.id, email="u@b.test", username="ub", name="U", password_hash="x")
    db_session.add(u)
    db_session.commit()

    resolved = get_current_household(db=db_session, user=u)
    assert resolved.id == b.id, "tenancy must come from the user, not the first household"


def test_high_confidence_cross_source_duplicates_auto_merge(db_session):
    """The same purchase captured by BOTH sms and gmail is a high-confidence duplicate; apply_dedup
    (which the syncs now call automatically) must link one to the other so totals never double-count.
    This is the engine behind 'the number is never wrong, no manual merge'."""
    from services import dedup

    h = Household(name="H")
    db_session.add(h)
    db_session.commit()
    common = dict(household_id=h.id, date="2026-06-15", amount=540.0, direction="debit",
                  bank="HDFC", card_type="Platinum", card_last4="9922", stmt_month="2026-06")
    db_session.add_all([
        Transaction(fingerprint="s1", description="BLINKIT", source="sms", **common),
        Transaction(fingerprint="g1", description="Blinkit", source="gmail", **common),
    ])
    db_session.commit()

    res = dedup.apply_dedup(db_session, only_high=True, household_id=h.id)
    assert res["merged_rows"] == 1, res
    # exactly one row remains active (dup_of_id IS NULL) — the headline can't double-count
    active = db_session.query(Transaction).filter(Transaction.dup_of_id.is_(None)).count()
    assert active == 1


def test_cross_source_duplicate_with_different_cards_merges(db_session):
    """The #1 real bug (seen live): the SAME purchase captured by SMS and by the statement, but the
    two channels report DIFFERENT card last-4s (e.g. SMS '0000' placeholder vs statement '3456').
    Identity is (amount, date) — the card must NOT split them into separate, un-mergeable blocks."""
    from services import dedup

    h = Household(name="H")
    db_session.add(h)
    db_session.commit()
    common = dict(household_id=h.id, date="2026-06-05", amount=5000.0, direction="debit",
                  bank="ICICI", card_type="x", stmt_month="2026-06")
    db_session.add_all([
        Transaction(fingerprint="sms1", description="Home Loan EMI", source="sms",
                    card_last4="0000", **common),
        Transaction(fingerprint="st1", description="Example Bank Home Loan", source="statement",
                    card_last4="3456", **common),
    ])
    db_session.commit()

    res = dedup.apply_dedup(db_session, only_high=True, household_id=h.id)
    assert res["merged_rows"] == 1, f"different-card cross-source duplicate not merged: {res}"
    # the STATEMENT row survives (source rank); the SMS provisional is linked to it
    active = db_session.query(Transaction).filter(Transaction.dup_of_id.is_(None)).all()
    assert len(active) == 1 and active[0].source == "statement"


def _dtxn(hid, fp, amount, date, source, merchant, card="9922", occurred_at=None, bank_ref=None):
    return Transaction(household_id=hid, fingerprint=fp, date=date, description=merchant,
                       merchant=merchant, source=source, amount=amount, direction="debit",
                       bank="HDFC", card_type="x", card_last4=card, stmt_month=date[:7],
                       occurred_at=occurred_at, bank_ref=bank_ref)


def test_tier1_timestamp_match_merges_despite_different_date_and_name(db_session):
    """Tier 1: same amount + timestamps within 15 min = the SAME transaction, even if the dates differ
    (cross-midnight) and the merchant names differ (Grofers vs Blink Commerce). Timestamp dominates."""
    from datetime import datetime
    from services import dedup
    h = Household(name="H"); db_session.add(h); db_session.commit()
    db_session.add_all([
        _dtxn(h.id, "a", 1234.0, "2026-06-05", "sms", "GROFERS", occurred_at=datetime(2026, 6, 5, 23, 58)),
        _dtxn(h.id, "b", 1234.0, "2026-06-06", "gmail", "Blink Commerce", occurred_at=datetime(2026, 6, 6, 0, 3)),
    ]); db_session.commit()
    assert dedup.apply_dedup(db_session, only_high=True, household_id=h.id)["merged_rows"] == 1


def test_far_apart_timestamps_not_tier1_merged(db_session):
    """Same amount but timestamps 9 hours apart (two real ₹200 metro rides) → NOT a Tier-1 merge."""
    from datetime import datetime
    from services import dedup
    h = Household(name="H"); db_session.add(h); db_session.commit()
    db_session.add_all([
        _dtxn(h.id, "a", 200.0, "2026-06-05", "sms", "Metro", occurred_at=datetime(2026, 6, 5, 9, 0)),
        _dtxn(h.id, "b", 200.0, "2026-06-05", "sms", "Metro", occurred_at=datetime(2026, 6, 5, 18, 0)),
    ]); db_session.commit()
    assert dedup.apply_dedup(db_session, only_high=True, household_id=h.id)["merged_rows"] == 0


def test_date_drift_cross_source_duplicate_merges(db_session):
    """The SAME txn captured by SMS and the statement on dates 1-2 days apart (banks report a
    different date per channel) must auto-merge — exact-date matching silently missed these."""
    from services import dedup
    h = Household(name="H"); db_session.add(h); db_session.commit()
    db_session.add_all([
        _dtxn(h.id, "a", 649.0, "2026-06-05", "sms", "Grofers"),
        _dtxn(h.id, "b", 649.0, "2026-06-07", "statement", "Grofers"),
    ]); db_session.commit()
    assert dedup.apply_dedup(db_session, only_high=True, household_id=h.id)["merged_rows"] == 1


def test_date_drift_different_merchants_not_auto_merged(db_session):
    """Two DIFFERENT same-amount purchases a few days apart, each caught by one channel, must NOT be
    auto-merged just because their dates are close — the name guard protects the multi-day case."""
    from services import dedup
    h = Household(name="H"); db_session.add(h); db_session.commit()
    db_session.add_all([
        _dtxn(h.id, "a", 500.0, "2026-06-05", "sms", "Swiggy"),
        _dtxn(h.id, "b", 500.0, "2026-06-08", "gmail", "Amazon"),
    ]); db_session.commit()
    assert dedup.apply_dedup(db_session, only_high=True, household_id=h.id)["merged_rows"] == 0


def test_different_merchants_same_amount_dont_contaminate_clusters(db_session):
    """Two DIFFERENT merchants that share an amount a couple of days apart must NOT chain into one
    cluster. Each is a clean cross-source (3 channels / 2 channels) duplicate and must auto-merge on
    its own — the old flat date-window linked them and buried both in review."""
    from services import dedup
    h = Household(name="H"); db_session.add(h); db_session.commit()
    db_session.add_all([
        # Suman ₹200 across 3 channels on the 13th — one payment, should merge (2 dups removed)
        _dtxn(h.id, "s1", 200.0, "2026-06-13", "gmail", "Suman Kumari Agarwal"),
        _dtxn(h.id, "s2", 200.0, "2026-06-13", "sms", "SUMAN KUMARI AGARWAL"),
        _dtxn(h.id, "s3", 200.0, "2026-06-13", "statement", "Suman Kumari Agarwal"),
        # Mahadev ₹200 across 2 channels on the 11th — one payment, should merge (1 dup removed)
        _dtxn(h.id, "m1", 200.0, "2026-06-11", "gmail", "Mahadev Enterprises"),
        _dtxn(h.id, "m2", 200.0, "2026-06-11", "sms", "MAHADEV ENT"),
    ]); db_session.commit()
    assert dedup.apply_dedup(db_session, only_high=True, household_id=h.id)["merged_rows"] == 3   # 2 (Suman) + 1 (Mahadev)


def test_provider_alias_merges_across_days(db_session):
    """Provider identity (step 5): 'Grofers' and 'Blink Commerce' are the same business (Blinkit). The
    same amount captured by two channels 3 days apart (no timestamp on either) must still auto-merge —
    the alias map collapses the different spellings so the multi-day name guard doesn't block them."""
    from services import dedup
    h = Household(name="H"); db_session.add(h); db_session.commit()
    db_session.add_all([
        _dtxn(h.id, "a", 780.0, "2026-06-05", "sms", "GROFERS"),
        _dtxn(h.id, "b", 780.0, "2026-06-08", "statement", "Blink Commerce Pvt Ltd"),
    ]); db_session.commit()
    assert dedup.apply_dedup(db_session, only_high=True, household_id=h.id)["merged_rows"] == 1


def test_different_providers_within_timestamp_not_merged(db_session):
    """Provider conflict beats even a Tier-1 timestamp match: a ₹500 Swiggy food order and a ₹500
    Instamart grocery order 5 min apart are TWO transactions, not one. Must not auto-merge."""
    from datetime import datetime
    from services import dedup
    h = Household(name="H"); db_session.add(h); db_session.commit()
    db_session.add_all([
        _dtxn(h.id, "a", 500.0, "2026-06-05", "sms", "Swiggy", occurred_at=datetime(2026, 6, 5, 20, 0)),
        _dtxn(h.id, "b", 500.0, "2026-06-05", "gmail", "Swiggy Instamart", occurred_at=datetime(2026, 6, 5, 20, 5)),
    ]); db_session.commit()
    assert dedup.apply_dedup(db_session, only_high=True, household_id=h.id)["merged_rows"] == 0
    assert len(dedup.preview(db_session, household_id=h.id)["groups"]) == 1                            # surfaced for review


def test_two_different_people_same_amount_close_time_not_grouped(db_session):
    """Regression: the cook (₹7,000) and the maid (₹7,000) paid a minute apart are TWO different people
    — timestamp proximity must NEVER cluster them. Different real names with nothing in common are kept
    fully apart: not merged, not even surfaced for review."""
    from datetime import datetime
    from services import dedup
    h = Household(name="H"); db_session.add(h); db_session.commit()
    db_session.add_all([
        _dtxn(h.id, "a", 7000.0, "2026-07-01", "gmail", "DEEPA RAO", occurred_at=datetime(2026, 7, 1, 17, 25)),
        _dtxn(h.id, "b", 7000.0, "2026-07-01", "gmail", "Meera Sundaram Iyer", occurred_at=datetime(2026, 7, 1, 17, 24)),
    ]); db_session.commit()
    assert dedup.apply_dedup(db_session, only_high=True, household_id=h.id)["merged_rows"] == 0
    assert len(dedup.preview(db_session, household_id=h.id)["groups"]) == 0                            # never grouped at all


def test_known_provider_and_different_person_same_amount_not_grouped(db_session):
    """A known provider on one row cannot make an unrelated real named payee compatible. This protects
    the fallback date-window tier, not just the timestamp tier."""
    from services import dedup
    h = Household(name="H"); db_session.add(h); db_session.commit()
    db_session.add_all([
        _dtxn(h.id, "a", 7000.0, "2026-07-01", "sms", "Swiggy"),
        _dtxn(h.id, "b", 7000.0, "2026-07-01", "gmail", "Meera Sundaram Iyer"),
    ]); db_session.commit()
    assert dedup.apply_dedup(db_session, only_high=True, household_id=h.id)["merged_rows"] == 0
    assert len(dedup.preview(db_session, household_id=h.id)["groups"]) == 0


def test_different_providers_same_day_cross_source_not_merged(db_session):
    """Same day, cross-source, same amount, but different known providers → previously auto-merged as
    'cross-source high'; the provider guard now holds it for review instead of silently merging."""
    from services import dedup
    h = Household(name="H"); db_session.add(h); db_session.commit()
    db_session.add_all([
        _dtxn(h.id, "a", 350.0, "2026-06-05", "sms", "Zepto"),
        _dtxn(h.id, "b", 350.0, "2026-06-05", "gmail", "Blinkit"),
    ]); db_session.commit()
    assert dedup.apply_dedup(db_session, only_high=True, household_id=h.id)["merged_rows"] == 0


def test_same_source_repeat_not_flagged_as_duplicate(db_session):
    """The multiple-SIP guarantee: two SAME-source rows of the same amount (a statement re-upload OR two
    genuine identical debits — indistinguishable without a bank ref) are NEVER flagged as duplicates.
    They show as the separate real transactions they most likely are. Real repeats are never hidden."""
    from services import dedup
    h = Household(name="H"); db_session.add(h); db_session.commit()
    db_session.add_all([
        _dtxn(h.id, "a", 12345.67, "2026-06-03", "statement", "Northgate Finance"),
        _dtxn(h.id, "b", 12345.67, "2026-06-05", "statement", "Northgate Finance"),
    ]); db_session.commit()
    assert len(dedup.preview(db_session, household_id=h.id)["groups"]) == 0
    assert dedup.auto_merge(db_session, household_id=h.id)["merged_rows"] == 0


def test_same_source_specific_amount_not_auto_merged(db_session):
    """Even a SPECIFIC odd amount (₹12,345.67) repeated on ONE channel is no longer auto-merged — with
    no bank reference we can't prove re-upload vs two real debits. Cross-source is the only auto-merge.
    (Supersedes the old 'specific same-source re-upload merges' rule.)"""
    from services import dedup
    h = Household(name="H"); db_session.add(h); db_session.commit()
    db_session.add_all([
        _dtxn(h.id, "a", 12345.67, "2026-06-03", "statement", "Northgate Finance"),
        _dtxn(h.id, "b", 12345.67, "2026-06-05", "statement", "Northgate Finance"),
    ]); db_session.commit()
    assert dedup.auto_merge(db_session, household_id=h.id)["merged_rows"] == 0


def test_same_bank_ref_merges_across_date_and_source(db_session):
    """Tier 0: identical UPI reference = the same real debit, even reported on different dates by
    different channels. The bank's own id leaves zero ambiguity → merge with certainty."""
    from services import dedup
    h = Household(name="H"); db_session.add(h); db_session.commit()
    db_session.add_all([
        _dtxn(h.id, "a", 2400.0, "2026-07-01", "sms", "Example Logistics", bank_ref="400100200300"),
        _dtxn(h.id, "b", 2400.0, "2026-07-03", "statement", "Example Logistics Ltd", bank_ref="400100200300"),
    ]); db_session.commit()
    assert dedup.apply_dedup(db_session, only_high=True, household_id=h.id)["merged_rows"] == 1


def test_different_bank_ref_never_merges(db_session):
    """The negative guard: two ₹500 debits to the same merchant on the same day but with DIFFERENT UPI
    refs are TWO real debits. The ref hard-blocks the merge even though amount+date+name all match."""
    from services import dedup
    h = Household(name="H"); db_session.add(h); db_session.commit()
    db_session.add_all([
        _dtxn(h.id, "a", 500.0, "2026-07-01", "sms", "Peoples Mart", bank_ref="111111111111"),
        _dtxn(h.id, "b", 500.0, "2026-07-01", "gmail", "Peoples Mart", bank_ref="222222222222"),
    ]); db_session.commit()
    assert dedup.apply_dedup(db_session, only_high=True, household_id=h.id)["merged_rows"] == 0
    assert len(dedup.preview(db_session, household_id=h.id)["groups"]) == 0


def test_statement_authoritative_folds_captures_to_one(db_session):
    """One flight = one statement line. Its duplicate email + 2 SMS captures fold into that one line →
    a single survivor, no inflated spend. (SMS fired twice, but the statement says one debit.)"""
    from services import dedup
    h = Household(name="H"); db_session.add(h); db_session.commit()
    db_session.add_all([
        _dtxn(h.id, "f1", 15500.0, "2026-06-14", "statement", "CT Flight Via Smartbuy"),
        _dtxn(h.id, "f2", 15500.0, "2026-06-14", "gmail", "CT FLIGHT VIA SMARTBUY"),
        _dtxn(h.id, "f3", 15500.0, "2026-06-14", "sms", "CT FLIGHT V"),
        _dtxn(h.id, "f4", 15500.0, "2026-06-14", "sms", "Cleartrip Flight"),
    ]); db_session.commit()
    dedup.auto_merge(db_session, household_id=h.id)
    flt = db_session.query(Transaction).filter(Transaction.amount == 15500.0, Transaction.dup_of_id.is_(None)).all()
    assert len(flt) == 1 and flt[0].source == "statement"


def test_statement_count_preserves_multiple_sips_with_sms(db_session):
    """Two real ₹10k Groww SIPs (2 statement lines) plus 3 SMS captures → 2 survivors, not 1. The
    statement's count of real debits is preserved; the SMS fold in. Real repeats never hidden."""
    from services import dedup
    h = Household(name="H"); db_session.add(h); db_session.commit()
    db_session.add_all([
        _dtxn(h.id, "s1", 10000.0, "2026-06-06", "statement", "Groww"),
        _dtxn(h.id, "s2", 10000.0, "2026-06-06", "statement", "Groww"),
        _dtxn(h.id, "m1", 10000.0, "2026-06-06", "sms", "Groww"),
        _dtxn(h.id, "m2", 10000.0, "2026-06-06", "sms", "Groww"),
        _dtxn(h.id, "m3", 10000.0, "2026-06-06", "sms", "Groww"),
    ]); db_session.commit()
    dedup.auto_merge(db_session, household_id=h.id)
    active = db_session.query(Transaction).filter(Transaction.amount == 10000.0, Transaction.dup_of_id.is_(None)).all()
    assert len(active) == 2 and all(r.source == "statement" for r in active)


def test_multiple_same_amount_sips_kept_separate(db_session):
    """A real-world case: multiple identical SIPs/transfers on one channel must stay as distinct
    transactions — never merged, never flagged. ₹10,000 Groww on three dates = three real SIPs."""
    from services import dedup
    h = Household(name="H"); db_session.add(h); db_session.commit()
    db_session.add_all([
        _dtxn(h.id, "a", 10000.0, "2026-06-04", "statement", "Groww"),
        _dtxn(h.id, "b", 10000.0, "2026-06-05", "statement", "Groww"),
        _dtxn(h.id, "c", 10000.0, "2026-06-06", "statement", "Groww"),
    ]); db_session.commit()
    assert dedup.auto_merge(db_session, household_id=h.id)["merged_rows"] == 0
    assert len(dedup.preview(db_session, household_id=h.id)["groups"]) == 0
    active = db_session.query(Transaction).filter(Transaction.dup_of_id.is_(None)).count()
    assert active == 3


def test_categorize_moves_person_transfer_out_of_spend(db_session, monkeypatch):
    """A SPEND row the classifier recognises as a person transfer must become 'Internal Transfer' AND
    have its txn_type flipped to TRANSFER, so it LEAVES the spend total (the ₹10L Personal/Misc bug —
    transfers were stuck as spend because txn_type never followed the category)."""
    monkeypatch.setattr("services.llm.available", lambda: True)  # proceed without a real API key
    from api.transactions import run_categorize
    from services import learned

    h = Household(name="H")
    db_session.add(h)
    db_session.commit()
    # teach the store this payee is a transfer (as a user correction would); makes the AI call a no-op
    learned.remember(db_session, learned.signature("PRAKASH GURJAR"),
                     "Internal Transfer", "Prakash Gurjar", "user")
    t = Transaction(household_id=h.id, fingerprint="p1", date="2026-06-20", description="PRAKASH GURJAR",
                    source="sms", amount=2500.0, direction="debit", category="Personal / Misc",
                    txn_type="SPEND", confidence="low", bank="HDFC", card_type="x",
                    card_last4="0000", stmt_month="2026-06", merchant=None)
    db_session.add(t)
    db_session.commit()

    run_categorize(db_session, limit=50, household_id=h.id)
    db_session.refresh(t)
    assert t.category == "Internal Transfer"
    assert t.txn_type == "TRANSFER"   # the key fix — no longer counted as spend


def _loan(household_id: int, name: str = "Car") -> Loan:
    return Loan(
        household_id=household_id, name=name, lender="HDFC", holder="X",
        category="Car", original_principal=500000.0, annual_rate_pct=10.0,
        tenure_months=60, disbursement_date=datetime.date(2025, 1, 1),
    )


def test_cannot_edit_another_households_loan(db_session, client_for):
    a, b = Household(name="A"), Household(name="B")
    db_session.add_all([a, b])
    db_session.commit()
    la = _loan(a.id)
    db_session.add(la)
    db_session.commit()

    # acting as B, try to edit A's loan by id → must 404
    body = {
        "name": "Hacked", "lender": "HDFC", "holder": "X", "category": "Car",
        "original_principal": 1.0, "annual_rate_pct": 1.0, "tenure_months": 12,
        "disbursement_date": "2025-01-01",
    }
    resp = client_for(b).put(f"/api/debt/{la.id}", json=body)
    assert resp.status_code == 404
    db_session.expire_all()
    assert db_session.get(Loan, la.id).name == "Car"


def test_debt_listing_scoped(db_session, client_for):
    a, b = Household(name="A"), Household(name="B")
    db_session.add_all([a, b])
    db_session.commit()
    db_session.add_all([_loan(a.id, "A-loan"), _loan(b.id, "B-loan")])
    db_session.commit()

    # acting as B, GET /debt must return exactly B's single loan
    resp = client_for(b).get("/api/debt")
    assert resp.status_code == 200
    assert len(resp.json()["loans"]) == 1


def _cashline(household_id: int, kind: str = "income", label: str = "Salary",
              amount: float = 100000.0) -> CashflowLine:
    return CashflowLine(household_id=household_id, kind=kind, label=label, amount_inr=amount)


def test_cannot_edit_another_households_cashflow_line(db_session, client_for):
    a, b = Household(name="A"), Household(name="B")
    db_session.add_all([a, b])
    db_session.commit()
    la = _cashline(a.id, label="Rent", amount=50000.0)
    db_session.add(la)
    db_session.commit()

    # acting as B, try to edit A's cashflow line by id → must 404
    body = {"kind": "income", "label": "Hacked", "amount_inr": 1.0}
    resp = client_for(b).put(f"/api/cashflow/{la.id}", json=body)
    assert resp.status_code == 404
    db_session.expire_all()
    assert db_session.get(CashflowLine, la.id).label == "Rent"


def test_cashflow_lines_scoped(db_session, client_for):
    a, b = Household(name="A"), Household(name="B")
    db_session.add_all([a, b])
    db_session.commit()
    # only A has an income line; B has none
    db_session.add(_cashline(a.id, kind="income", label="Salary", amount=100000.0))
    db_session.commit()

    # acting as A, the income line shows up; acting as B it must NOT leak in
    assert [l["label"] for l in client_for(a).get("/api/cashflow").json()["income"]] == ["Salary"]
    assert client_for(b).get("/api/cashflow").json()["income"] == []


def test_health_composed_path_is_scoped(db_session, client_for):
    """The cockpit /health endpoint COMPOSES compute_cashflow. Acting as B, its income must reflect
    ONLY B's cashflow lines — never A's. Guards the composed (multi-engine) request path, not just
    the per-domain listing endpoints."""
    a, b = Household(name="A"), Household(name="B")
    db_session.add_all([a, b])
    db_session.commit()
    # distinct income for each household
    db_session.add_all([
        _cashline(a.id, kind="income", label="A salary", amount=500000.0),
        _cashline(b.id, kind="income", label="B salary", amount=120000.0),
    ])
    db_session.commit()

    # acting as B, the composed health payload must show B's income only (not A's 500000, not the sum)
    resp = client_for(b).get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["income"] == 120000.0, f"composed path leaked another household's income: {resp.json()}"

    # and acting as A shows A's own income
    assert client_for(a).get("/api/health").json()["income"] == 500000.0


def _asset(household_id: int, name: str = "FD", value: float = 50000.0) -> Asset:
    # use a non-market type (cash) so valuation never hits the price feed in tests
    return Asset(household_id=household_id, name=name, asset_type="cash",
                 currency="INR", manual_value_inr=value, active=True)


def test_cannot_edit_another_households_asset(db_session, client_for):
    a, b = Household(name="A"), Household(name="B")
    db_session.add_all([a, b])
    db_session.commit()
    aa = _asset(a.id)
    db_session.add(aa)
    db_session.commit()

    # acting as B, try to edit A's asset by id → must 404, and A's asset must be untouched
    body = {"name": "Hacked", "asset_type": "cash", "currency": "INR", "manual_value_inr": 1.0}
    resp = client_for(b).put(f"/api/networth/{aa.id}", json=body)
    assert resp.status_code == 404
    db_session.expire_all()
    assert db_session.get(Asset, aa.id).name == "FD"


def test_networth_assets_scoped(db_session, client_for):
    a, b = Household(name="A"), Household(name="B")
    db_session.add_all([a, b])
    db_session.commit()
    db_session.add_all([_asset(a.id, "A-FD"), _asset(b.id, "B-FD")])
    db_session.commit()

    # GET /networth queries only the assets table — acting as B must see exactly B's one asset
    resp = client_for(b).get("/api/networth")
    assert resp.status_code == 200
    assert [x["name"] for x in resp.json()["assets"]] == ["B-FD"]


def _budget(household_id: int, category: str = "Groceries", limit: float = 22000.0,
            month: str = "2026-06") -> Budget:
    return Budget(household_id=household_id, category=category,
                  monthly_limit_inr=limit, month=month)


def test_budget_listing_scoped(db_session, client_for):
    a, b = Household(name="A"), Household(name="B")
    db_session.add_all([a, b])
    db_session.commit()
    # only A has a budget row for Groceries; B has none
    db_session.add(_budget(a.id, category="Groceries", limit=22000.0, month="2026-06"))
    db_session.commit()

    # GET /budget reads only the budgets + transactions tables. Acting as B (no rows, no txns),
    # no category should carry A's 22000 limit — every limit must be 0.
    resp = client_for(b).get("/api/budget?month=2026-06&mode=month")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert all(it["limit_inr"] == 0.0 for it in items), f"leaked another household's budget: {items}"

    # Acting as A, the Groceries limit must show through.
    resp_a = client_for(a).get("/api/budget?month=2026-06&mode=month")
    groceries = next(it for it in resp_a.json()["items"] if it["category"] == "Groceries")
    assert groceries["limit_inr"] == 22000.0


# ── Sinking funds ────────────────────────────────────────────
def _fund(household_id: int, name: str = "Home tickets") -> SinkingFund:
    return SinkingFund(household_id=household_id, name=name, monthly_contribution=5000.0,
                       opening_balance=10000.0, as_of_date=datetime.date(2025, 1, 1), active=True)


def test_cannot_edit_another_households_sinking_fund(db_session, client_for):
    a, b = Household(name="A"), Household(name="B")
    db_session.add_all([a, b])
    db_session.commit()
    fa = _fund(a.id)
    db_session.add(fa)
    db_session.commit()

    # acting as B, try to edit A's fund by id → must 404, and A's fund must be untouched
    body = {"name": "Hacked", "monthly_contribution": 1.0, "opening_balance": 1.0,
            "as_of_date": "2025-01-01"}
    resp = client_for(b).put(f"/api/sinking/{fa.id}", json=body)
    assert resp.status_code == 404
    db_session.expire_all()
    assert db_session.get(SinkingFund, fa.id).name == "Home tickets"


# ── Recurring investments ────────────────────────────────────
def _recurring(household_id: int, name: str = "EPF") -> RecurringInvestment:
    return RecurringInvestment(household_id=household_id, name=name, kind="epf",
                               opening_balance=100000.0, as_of_date=datetime.date(2025, 1, 1),
                               annual_rate_pct=8.25, compounding="annual", active=True)


def test_cannot_edit_another_households_recurring_investment(db_session, client_for):
    a, b = Household(name="A"), Household(name="B")
    db_session.add_all([a, b])
    db_session.commit()
    ra = _recurring(a.id)
    db_session.add(ra)
    db_session.commit()

    # acting as B, try to edit A's investment by id → must 404, and A's row must be untouched
    body = {"name": "Hacked", "kind": "epf", "opening_balance": 1.0, "as_of_date": "2025-01-01"}
    resp = client_for(b).put(f"/api/recurring/{ra.id}", json=body)
    assert resp.status_code == 404
    db_session.expire_all()
    assert db_session.get(RecurringInvestment, ra.id).name == "EPF"


# ── Insurance policies ───────────────────────────────────────
def _policy(household_id: int, provider: str = "Max Life") -> InsurancePolicy:
    return InsurancePolicy(household_id=household_id, provider=provider, kind="term",
                           sum_assured=10000000.0, premium=2000.0, premium_frequency="monthly",
                           active=True)


def test_cannot_edit_another_households_insurance_policy(db_session, client_for):
    a, b = Household(name="A"), Household(name="B")
    db_session.add_all([a, b])
    db_session.commit()
    pa = _policy(a.id)
    db_session.add(pa)
    db_session.commit()

    # acting as B, try to edit A's policy by id → must 404, and A's row must be untouched
    body = {"provider": "Hacked", "kind": "term", "sum_assured": 1.0, "premium": 1.0,
            "premium_frequency": "monthly"}
    resp = client_for(b).put(f"/api/insurance/{pa.id}", json=body)
    assert resp.status_code == 404
    db_session.expire_all()
    assert db_session.get(InsurancePolicy, pa.id).provider == "Max Life"


# ── Category rules (learned classifier store) ────────────────
def test_learned_store_optional_household_scoping(db_session):
    """learned.remember(..., household_id=A) then lookup(..., household_id=B) must miss, while
    lookup(..., household_id=A) finds it — proving the optional per-household scoping works."""
    from services import learned

    a, b = Household(name="A"), Household(name="B")
    db_session.add_all([a, b])
    db_session.commit()

    learned.remember(db_session, "SALON ESSENTIAL", "Personal Care", "Salon Essential",
                     household_id=a.id)
    db_session.commit()

    assert learned.lookup(db_session, "SALON ESSENTIAL", household_id=b.id) is None
    hit = learned.lookup(db_session, "SALON ESSENTIAL", household_id=a.id)
    assert hit is not None and hit.category == "Personal Care"


# ── Cycle / spend engine ─────────────────────────────────────
def _spend_txn(household_id: int, fp: str, amount: float, category: str = "Groceries") -> Transaction:
    """An in-cycle everyday-SPEND debit (today's date so it always lands in the current cycle)."""
    return Transaction(
        household_id=household_id, fingerprint=fp, date=date.today().isoformat(),
        description="X", source="manual", amount=amount, direction="debit",
        category=category, txn_type="SPEND", bank="Cash", card_type="cash",
        card_last4="0000", stmt_month=date.today().strftime("%Y-%m"),
    )


def test_card_cycle_spend_is_scoped_to_the_household(db_session):
    """card_cycle_spend must sum ONLY the acting household's in-cycle spend — never A+B. Catches the
    multi-tenant leak where the cycle engine queried transactions without a household filter."""
    from services.cycle import card_cycle_spend

    a, b = Household(name="A"), Household(name="B")
    db_session.add_all([a, b])
    db_session.commit()

    # one in-cycle SPEND each: A=100, B=300
    db_session.add_all([_spend_txn(a.id, "a1", 100.0), _spend_txn(b.id, "b1", 300.0)])
    db_session.commit()

    # acting on B, we must see ONLY B's 300 — not 400 (A+B)
    assert card_cycle_spend(db_session, date.today(), household_id=b.id) == 300.0
    assert card_cycle_spend(db_session, date.today(), household_id=a.id) == 100.0


# --- dedup and reconcile, the path that was never scoped -------------------------------------
#
# The 41 tests above cover transactions, loans, cashflow, assets, budgets, sinking funds, recurring
# investments and insurance. The dedup and sync path was the gap, and it is where six endpoints
# operated on every row in the database before it was fixed.

def _dup_pair(hid: int, tag: str) -> list[Transaction]:
    """One purchase captured twice, by two channels — the shape auto_merge is meant to fold."""
    return [
        Transaction(household_id=hid, fingerprint=f"{tag}-sms", date="2026-07-04",
                    description="ZOMATO", merchant="ZOMATO", source="sms", amount=531.09,
                    direction="debit", category="Food & Dining", txn_type="SPEND", bank="HDFC",
                    card_type="credit", card_last4="9922", stmt_month="2026-07"),
        Transaction(household_id=hid, fingerprint=f"{tag}-stmt", date="2026-07-04",
                    description="ZOMATO LIMITED", merchant="ZOMATO LIMITED", source="statement",
                    amount=531.09, direction="debit", category="Food & Dining", txn_type="SPEND",
                    bank="HDFC", card_type="credit", card_last4="9922", stmt_month="2026-07"),
    ]


def _two_households(db_session) -> tuple[Household, Household]:
    a, b = Household(name="dupA"), Household(name="dupB")
    db_session.add_all([a, b])
    db_session.commit()
    db_session.add_all(_dup_pair(a.id, "a") + _dup_pair(b.id, "b"))
    db_session.commit()
    return a, b


def _live(db_session, hid: int) -> int:
    return (db_session.query(Transaction)
            .filter(Transaction.household_id == hid, Transaction.dup_of_id.is_(None)).count())


def test_duplicate_preview_only_reports_its_own_household(db_session):
    from services import dedup

    a, b = _two_households(db_session)
    groups = dedup.preview(db_session, household_id=a.id)["groups"]
    ids = {row["id"] for g in groups for row in [g["primary"], *g["duplicates"]]}
    owners = {db_session.get(Transaction, i).household_id for i in ids}
    assert owners == {a.id}, f"preview for household {a.id} returned rows owned by {owners}"


def test_merging_in_one_household_leaves_the_other_untouched(db_session):
    """The failure this prevents is silent and permanent: household B's two captures folded into one
    because household A ran a merge, so B's ledger loses a row it never asked anyone to touch."""
    from services import dedup

    a, b = _two_households(db_session)
    assert _live(db_session, b.id) == 2

    dedup.auto_merge(db_session, household_id=a.id)

    assert _live(db_session, a.id) == 1, "household A's own duplicate did not merge"
    assert _live(db_session, b.id) == 2, "a merge in household A changed household B"


def test_undo_in_one_household_does_not_restore_the_other(db_session):
    from services import dedup

    a, b = _two_households(db_session)
    dedup.auto_merge(db_session, household_id=a.id)
    dedup.auto_merge(db_session, household_id=b.id)
    assert _live(db_session, a.id) == 1 and _live(db_session, b.id) == 1

    dedup.undo(db_session, household_id=a.id)

    assert _live(db_session, a.id) == 2, "A's own undo did nothing"
    assert _live(db_session, b.id) == 1, "an undo in household A un-merged household B"


def test_internal_transfer_pairing_never_crosses_households(db_session):
    """A debit in one household and a credit of the same amount in another are not a transfer pair,
    and tagging them as one would remove real spend from both ledgers."""
    from services.reconcile import pair_internal_transfers

    a, b = Household(name="revA"), Household(name="revB")
    db_session.add_all([a, b])
    db_session.commit()

    def acct(hid, fp, direction):
        return Transaction(household_id=hid, fingerprint=fp, date="2026-07-04",
                           description="TRANSFER", merchant="TRANSFER", source="statement",
                           amount=25000.0, direction=direction, category="Transfer",
                           txn_type="TRANSFER", bank="HDFC", card_type="account",
                           card_last4="1111", stmt_month="2026-07")

    db_session.add_all([acct(a.id, "a-debit", "debit"), acct(b.id, "b-credit", "credit")])
    db_session.commit()

    res = pair_internal_transfers(db_session, household_id=a.id)
    assert res.get("pairs", 0) == 0, (
        "a debit in household A was paired with a credit in household B"
    )
