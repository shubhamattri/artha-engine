"""Cross-source de-duplication safety net.

The key regression: the SMS pipeline can double-ingest one debit — two rows, same card, same
paise-precise amount, same merchant, same DAY, but with occurred_at drifted hours apart (so the
15-minute timestamp tier can't see them) and different fingerprints (so ingest dedup can't either).
Those MUST be merged; genuine same-amount repeats (round SIP amounts, different days, two people)
must NOT be.
"""

from datetime import datetime

from models.tenant import Household
from models.transaction import Transaction
from services import dedup


def _sms(hid, amount, merchant, date="2026-07-03", occ=None, last4="9922", fp=None):
    return Transaction(
        household_id=hid, fingerprint=fp or f"fp-{hid}-{merchant}-{occ}", date=date,
        description=merchant, merchant=merchant, source="sms", amount=amount, direction="debit",
        category="Food & Dining", txn_type="SPEND", bank="HDFC", card_type="credit",
        card_last4=last4, stmt_month=date[:7], occurred_at=occ)


def test_double_ingested_sms_same_day_paise_merges(db_session):
    """The live bug: two SMS captures of one Zomato debit, ₹531.09, card 9922, 2026-07-03, occurred_at
    5.5h apart → one must fold into the other (high-confidence auto-merge)."""
    h = Household(name="D"); db_session.add(h); db_session.commit()
    a = _sms(h.id, 531.09, "ZOMATO", occ=datetime(2026, 7, 3, 8, 47, 25), fp="a")
    b = _sms(h.id, 531.09, "ZOMATO LIMITED", occ=datetime(2026, 7, 3, 14, 17, 27), fp="b")
    db_session.add_all([a, b]); db_session.commit()

    dedup.auto_merge(db_session, household_id=h.id)                       # what sync calls
    linked = [t for t in (a, b) if t.dup_of_id is not None]
    assert len(linked) == 1, "exactly one of the pair should fold into the other"


def test_round_amount_known_merchant_merges(db_session):
    """A ROUND same-amount SMS pair for a KNOWN MERCHANT (Bundl = Swiggy, ₹696.00, same card, same
    day, truncated name) is one purchase double-captured — it MUST merge. The round-amount protection
    is reserved for provider-less repeats (SIPs/transfers), tested separately."""
    h = Household(name="R"); db_session.add(h); db_session.commit()
    a = _sms(h.id, 696.0, "Bundl Technologies", date="2026-06-22", occ=None, last4="8456", fp="ra")
    b = _sms(h.id, 696.0, "Bundl Techn", date="2026-06-22",
             occ=datetime(2026, 6, 22, 21, 55, 11), last4="8456", fp="rb")
    db_session.add_all([a, b]); db_session.commit()

    dedup.auto_merge(db_session, household_id=h.id)
    assert len([t for t in (a, b) if t.dup_of_id is not None]) == 1, "Swiggy round double-capture must merge"


def test_different_day_paise_not_merged(db_session):
    """Same paise amount, same merchant, but DIFFERENT days → two real orders, never merged."""
    h = Household(name="DD"); db_session.add(h); db_session.commit()
    a = _sms(h.id, 531.09, "ZOMATO", date="2026-07-03", occ=datetime(2026, 7, 3, 8, 47), fp="da")
    b = _sms(h.id, 531.09, "ZOMATO", date="2026-07-05", occ=datetime(2026, 7, 5, 9, 1), fp="db")
    db_session.add_all([a, b]); db_session.commit()

    dedup.auto_merge(db_session, household_id=h.id)
    assert a.dup_of_id is None and b.dup_of_id is None


def test_merchant_round_amount_same_day_merges(db_session):
    """REGRESSION (Blinkit ₹1500): a KNOWN merchant charging the same ROUND amount to the same card
    the same day = one purchase double-captured (bank SMS + merchant SMS), even though it's round and
    the name is truncated ('BLINK COMME' vs 'Blinkit'). Must merge."""
    h = Household(name="M"); db_session.add(h); db_session.commit()
    a = _sms(h.id, 1500.0, "Blinkit", occ=datetime(2026, 7, 3, 21, 28, 9), fp="ba")
    b = _sms(h.id, 1500.0, "BLINK COMME", occ=datetime(2026, 7, 3, 15, 58, 7), fp="bb")
    db_session.add_all([a, b]); db_session.commit()
    dedup.auto_merge(db_session, household_id=h.id)
    assert len([t for t in (a, b) if t.dup_of_id is not None]) == 1


def test_round_transfer_no_merchant_not_merged(db_session):
    """A ROUND same-amount pair with NO merchant provider (a SIP / transfer) must stay separate —
    the merchant relaxation must not touch investments/transfers."""
    h = Household(name="S"); db_session.add(h); db_session.commit()
    a = _sms(h.id, 5000.0, "SIP HDFC MF", occ=datetime(2026, 7, 3, 9, 0), fp="sa")
    b = _sms(h.id, 5000.0, "SIP HDFC MF", occ=datetime(2026, 7, 3, 18, 0), fp="sb")
    db_session.add_all([a, b]); db_session.commit()
    dedup.auto_merge(db_session, household_id=h.id)
    assert a.dup_of_id is None and b.dup_of_id is None
