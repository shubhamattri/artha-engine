"""Drill-down primitives on GET /transactions: the exact-id set and the inclusive date window — both
tenant-scoped."""

from models.tenant import Household
from models.transaction import Transaction


def _stxn(hid, amount, d, merchant="Shop"):
    return Transaction(household_id=hid, fingerprint=f"t-{hid}-{amount}-{d}-{merchant}", date=d,
                       description=merchant, merchant=merchant, source="statement", amount=amount,
                       direction="debit", category="Groceries", txn_type="SPEND", bank="HDFC",
                       card_type="credit", card_last4="1111", stmt_month=d[:7])


def test_start_end_window_is_inclusive_and_scoped(db_session, client_for):
    a = Household(name="A"); b = Household(name="B"); db_session.add_all([a, b]); db_session.commit()
    db_session.add_all([
        _stxn(a.id, 100.0, "2026-03-05"),   # inside
        _stxn(a.id, 200.0, "2026-03-31"),   # inside (boundary)
        _stxn(a.id, 300.0, "2026-04-01"),   # outside (after)
        _stxn(a.id, 400.0, "2026-02-28"),   # outside (before)
        _stxn(b.id, 999.0, "2026-03-10"),   # B's row — must never appear for A
    ]); db_session.commit()

    out = client_for(a).get("/api/transactions", params={"start": "2026-03-01", "end": "2026-03-31"}).json()
    amounts = sorted(t["amount"] for t in out["transactions"])
    assert amounts == [100.0, 200.0]        # inclusive window, B excluded, out-of-range excluded
