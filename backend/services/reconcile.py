"""Reconciliation engine (step 1).

Internal-transfer pairing: money that merely moves between the family's OWN bank
accounts must not be counted as income or spend. We match a debit in one
own-account to a credit in another (amount within ₹1, date within 4 days) and
mark BOTH as TRANSFER.

(Balance-gate proof needs per-statement opening/closing — parsers don't capture
those yet; that's the next slice of step 1.)
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from sqlalchemy.orm import Session

from models.transaction import Transaction

WINDOW_DAYS = 4
AMOUNT_TOL = 1.0


def _d(s: str) -> date | None:
    try:
        y, m, d = (int(x) for x in s[:10].split("-"))
        return date(y, m, d)
    except Exception:
        return None


def pair_internal_transfers(db: Session, *, household_id: int) -> dict:
    """Find & tag debit↔credit pairs across own accounts. Returns stats.

    `household_id` is required, not defaulted: this pairs and tags rows, and unscoped it would match
    one household's debit against another's credit of the same amount.
    """
    rows = (
        db.query(Transaction)
        .filter(Transaction.household_id == household_id,
                Transaction.card_type.ilike("%account%"))
        .all()
    )
    credits_by_amt: dict[int, list[Transaction]] = defaultdict(list)
    for r in rows:
        if r.direction == "credit":
            credits_by_amt[round(r.amount)].append(r)

    used: set[int] = set()
    pairs = 0
    eliminated = 0.0

    for d in rows:
        if d.direction != "debit" or d.id in used:
            continue
        dd = _d(d.date)
        for c in credits_by_amt.get(round(d.amount), []):
            if c.id in used or c.id == d.id:
                continue
            if c.card_last4 == d.card_last4:          # must be a DIFFERENT own account
                continue
            if abs(c.amount - d.amount) > AMOUNT_TOL:
                continue
            cd = _d(c.date)
            if dd and cd and abs((dd - cd).days) > WINDOW_DAYS:
                continue
            # matched pair → eliminate both
            used.add(d.id)
            used.add(c.id)
            for t in (d, c):
                t.txn_type = "TRANSFER"
                t.category = "Internal Transfer"
            pairs += 1
            eliminated += d.amount
            break

    db.commit()
    return {"pairs": pairs, "transactions_tagged": len(used), "eliminated_total": round(eliminated, 2)}
