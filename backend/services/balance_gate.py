"""Balance gate — anti-hallucination proof that a parse is correct.

An account's parsed transactions are trustworthy only if they reconcile to the
bank's own printed opening/closing balances: opening + credits - debits should
equal the printed closing (within a small tolerance). See ALGORITHM 2.

A failing gate means the columns were misread (e.g. amount/balance swapped) and
the pipeline must stop rather than publish numbers built on a bad parse.
"""

from __future__ import annotations

from sqlalchemy import func

from models.transaction import Transaction


def gate(
    opening: float,
    credits: float,
    debits: float,
    closing: float,
    tol: float = 1.0,
) -> dict:
    """Check whether opening + credits - debits ties to the printed closing.

    Args:
        opening: opening balance for the account (as printed/derived).
        credits: sum of all credit (deposit) amounts.
        debits: sum of all debit (withdrawal) amounts.
        closing: closing balance the bank actually printed.
        tol: amount tolerance in rupees (default ₹1.00).

    Returns:
        dict with:
          expected_closing — opening + credits - debits
          printed_closing  — the bank's printed closing
          ties             — True if |expected - printed| <= tol
          delta            — expected_closing - printed_closing
    """
    expected_closing = opening + credits - debits
    delta = expected_closing - closing
    return {
        "expected_closing": expected_closing,
        "printed_closing": closing,
        "ties": abs(delta) <= tol,
        "delta": delta,
    }


def verify_account(
    db_session,
    card_last4: str,
    opening: float,
    closing: float,
    tol: float = 1.0,
) -> dict:
    """Sum an account's stored credits/debits and run the balance gate.

    Pulls every Transaction for `card_last4` from the DB, sums credits and
    debits by `direction`, then calls gate() against the supplied opening and
    printed closing balances.

    Returns the gate() dict plus:
      credits     — summed credit amount for the account
      debits      — summed debit amount for the account
      card_last4  — the account checked
      txn_count   — number of transactions summed
    """
    rows = (
        db_session.query(
            Transaction.direction,
            func.coalesce(func.sum(Transaction.amount), 0.0),
            func.count(Transaction.id),
        )
        .filter(Transaction.card_last4 == card_last4)
        .group_by(Transaction.direction)
        .all()
    )

    credits = 0.0
    debits = 0.0
    txn_count = 0
    for direction, total, count in rows:
        amount = float(total or 0.0)
        txn_count += int(count or 0)
        if direction == "credit":
            credits += amount
        elif direction == "debit":
            debits += amount

    result = gate(opening, credits, debits, closing, tol=tol)
    result.update(
        {
            "credits": credits,
            "debits": debits,
            "card_last4": card_last4,
            "txn_count": txn_count,
        }
    )
    return result
