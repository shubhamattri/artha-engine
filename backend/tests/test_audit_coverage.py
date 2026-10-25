"""Audit coverage for the AUTOMATED paths — not just manual edits.

The audit trail must record changes the SYSTEM makes too: a quick-logged spend (create), an AI
recategorize (categorize), and a dedup merge (merge). These are written to FAIL before those paths
log and PASS after. Manual-edit coverage lives in test_tenant_isolation.py.
"""

from models.audit import TransactionEvent
from models.tenant import Household
from models.transaction import Transaction


def _txn(household_id: int, fp: str, amount: float, *, source: str = "sms",
         card_last4: str = "9922", date: str = "2026-06-01", merchant: str | None = None) -> Transaction:
    return Transaction(
        household_id=household_id, fingerprint=fp, date=date, description="X",
        merchant=merchant, source=source, amount=amount, direction="debit", bank="HDFC",
        card_type="x", card_last4=card_last4, stmt_month=date[:7],
    )


def test_quick_add_logs_a_create_event(db_session, client_for):
    """POST /transactions/quick (log-a-spend) must append a 'create' event for the new row."""
    a = Household(name="A")
    db_session.add(a)
    db_session.commit()

    resp = client_for(a).post("/api/transactions/quick",
                              json={"amount": 420.0, "note": "Chai", "category": "Food & Dining"})
    assert resp.status_code == 200, resp.text
    txn_id = resp.json()["id"]

    events = (db_session.query(TransactionEvent)
              .filter(TransactionEvent.txn_id == txn_id,
                      TransactionEvent.action == "create").all())
    assert len(events) == 1, f"expected one create event, got {events}"
    assert events[0].actor == "user"
    assert events[0].new_value == "420.0"


def test_dedup_merge_logs_a_merge_event(db_session):
    """A cross-source duplicate linked to its primary must append a 'merge' event by the 'dedup' actor."""
    from services import dedup

    a = Household(name="A")
    db_session.add(a)
    db_session.commit()

    # same household, same amount/date, two channels (sms + gmail), same card → cross-source duplicate
    t_sms = _txn(a.id, "dup-sms", 1500.0, source="sms")
    t_gmail = _txn(a.id, "dup-gmail", 1500.0, source="gmail")
    db_session.add_all([t_sms, t_gmail])
    db_session.commit()

    result = dedup.apply_dedup(db_session, only_high=True, household_id=a.id)
    assert result["merged_rows"] == 1, result

    # the linked duplicate is the one that got a dup_of_id stamped
    linked = (db_session.query(Transaction)
              .filter(Transaction.dup_of_id.isnot(None)).one())
    events = (db_session.query(TransactionEvent)
              .filter(TransactionEvent.txn_id == linked.id,
                      TransactionEvent.action == "merge").all())
    assert len(events) == 1, f"expected one merge event, got {events}"
    assert events[0].actor == "dedup"
    assert events[0].new_value == str(linked.dup_of_id)
