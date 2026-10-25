"""Audit-trail API — history, corrections, and undo, all scoped to the acting household.

Builds its own TestClient that mounts BOTH the transactions and audit routers (mirroring conftest's
client_for) so an edit made via PATCH /api/transactions and the audit endpoints share one DB session.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from models.audit import TransactionEvent
from models.tenant import Household
from models.transaction import Transaction


def _client(db_session, household):
    """A TestClient acting as `household`, wired to transactions + audit on the shared test session."""
    from api import audit, transactions
    from api.deps import get_current_household
    from database import get_db

    app = FastAPI()
    app.include_router(transactions.router, prefix="/api")
    app.include_router(audit.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_household] = lambda: household
    return TestClient(app)


def _txn(household_id: int, fp: str, amount: float) -> Transaction:
    return Transaction(
        household_id=household_id, fingerprint=fp, date="2026-06-01", description="X",
        source="manual", amount=amount, direction="debit", bank="HDFC", card_type="x",
        card_last4="0000", stmt_month="2026-06",
    )


def _household_with_txn(db_session, amount: float = 100.0):
    h = Household(name="A")
    db_session.add(h)
    db_session.commit()
    t = _txn(h.id, "a1", amount)
    db_session.add(t)
    db_session.commit()
    return h, t


def test_edit_shows_up_in_history(db_session):
    h, t = _household_with_txn(db_session, amount=100.0)
    client = _client(db_session, h)

    resp = client.patch(f"/api/transactions/{t.id}", json={"amount": 250.0})
    assert resp.status_code == 200

    history = client.get(f"/api/audit/history?txn_id={t.id}").json()
    amount_events = [e for e in history if e["field"] == "amount"]
    assert len(amount_events) == 1
    e = amount_events[0]
    assert e["action"] == "edit"
    assert e["old_value"] == "100.0" and e["new_value"] == "250.0"
    assert e["txn_id"] == t.id and e["actor"] == "user"
    assert e["created_at"] is not None


def test_undo_restores_old_value_and_logs_an_undo_event(db_session):
    h, t = _household_with_txn(db_session, amount=100.0)
    client = _client(db_session, h)

    client.patch(f"/api/transactions/{t.id}", json={"amount": 250.0})
    db_session.refresh(t)
    assert t.amount == 250.0

    edit_event = (db_session.query(TransactionEvent)
                  .filter(TransactionEvent.txn_id == t.id, TransactionEvent.field == "amount").first())

    resp = client.post(f"/api/audit/undo/{edit_event.id}")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "restored": t.id, "field": "amount"}

    db_session.refresh(t)
    assert t.amount == 100.0  # restored to the pre-edit value

    undo_events = (db_session.query(TransactionEvent)
                   .filter(TransactionEvent.txn_id == t.id, TransactionEvent.action == "undo").all())
    assert len(undo_events) == 1
    assert undo_events[0].new_value == "100.0" and undo_events[0].old_value == "250.0"


def test_undo_permanent_delete_is_rejected(db_session):
    h, t = _household_with_txn(db_session)
    client = _client(db_session, h)
    db_session.add(TransactionEvent(household_id=h.id, txn_id=t.id, action="delete",
                                    field="merchant", old_value="X", new_value=None, actor="user"))
    db_session.commit()
    del_event = (db_session.query(TransactionEvent)
                 .filter(TransactionEvent.action == "delete").first())

    resp = client.post(f"/api/audit/undo/{del_event.id}")
    assert resp.status_code == 400


def test_corrections_returns_expected_shape(db_session):
    h, t = _household_with_txn(db_session, amount=100.0)
    client = _client(db_session, h)

    client.patch(f"/api/transactions/{t.id}", json={"amount": 250.0})
    client.patch(f"/api/transactions/{t.id}", json={"category": "Groceries"})

    body = client.get("/api/audit/corrections").json()
    assert set(body.keys()) == {"edits_by_field", "top_recategorizations"}
    assert body["edits_by_field"].get("amount") == 1
    assert body["edits_by_field"].get("category") == 1
    assert {"to": "Groceries", "count": 1} in body["top_recategorizations"]


def test_history_is_scoped_to_the_household(db_session):
    """Acting as B, /audit/history must never surface A's events."""
    a, ta = _household_with_txn(db_session, amount=100.0)
    b = Household(name="B")
    db_session.add(b)
    db_session.commit()

    _client(db_session, a).patch(f"/api/transactions/{ta.id}", json={"amount": 500.0})

    b_history = _client(db_session, b).get("/api/audit/history").json()
    assert b_history == []
