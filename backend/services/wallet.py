"""The household wallet: what past cycles saved, minus what has been spent from it.

It accrues by itself. Every closed cycle that underspent its pool adds to the balance, with no step
to remember — an earmark you must perform before money is usable is a step that gets skipped, and
then the wallet permanently understates what is there.

It is NOT an asset. Those rupees already sit in a bank account whose balance is already in net
worth; adding the wallet would count the same money twice. Spending from it moves the same rupees
into a loan or an investment, so net worth only moves by the interest that avoids or earns.
"""

from __future__ import annotations

from datetime import date, datetime

from models.surplus import SurplusDraw
from sqlalchemy.orm import Session

SOURCES = ("wallet", "bank", "other")


def _closed_cycle_ends(db: Session, household_id: int, count: int = 24) -> list[str]:
    """The end date of each CLOSED cycle, newest first. The live cycle is excluded — its saving is
    not final and counting it would let you spend money the cycle may still consume."""
    from datetime import timedelta

    from services.cycle import cycle_bounds, household_start_day

    sd = household_start_day(db, household_id)
    cstart, _ = cycle_bounds(date.today(), sd)
    ends = []
    cur = cstart - timedelta(days=1)          # last day of the previous (closed) cycle
    for _ in range(count):
        ends.append(cur.isoformat())
        prev_start, _e = cycle_bounds(cur, sd)
        cur = prev_start - timedelta(days=1)
    return ends


def inflows(db: Session, household_id: int, count: int = 24) -> list[dict]:
    """Each closed cycle and what it saved."""
    from services.surplus import saved_in_cycle

    start_from = _start_cycle(db, household_id)
    if not start_from:
        return []                      # the wallet has not been started; nothing accrues
    out = []
    for end in _closed_cycle_ends(db, household_id, count):
        # Forward only. Everything before the start cycle is excluded because the data does not
        # support a claim either way — the wallet only speaks for cycles it actually watched.
        if end < start_from:
            continue
        # A cycle with NO transactions has no saving — it has no data. Without this the pool minus
        # zero spend reads as a full month saved, and cycles from before tracking began each added
        # the entire pool: the balance came out at Rs 30 lakh, most of it from months where the
        # only fact available was that we have no records.
        if not _has_data(db, household_id, end):
            continue
        saved = saved_in_cycle(db, household_id, end)
        if saved > 0:
            out.append({"cycle_end": end, "saved": saved})
    return out


def _start_cycle(db: Session, household_id: int) -> str | None:
    from models.tenant import Household
    h = db.query(Household).filter(Household.id == household_id).first()
    return getattr(h, "wallet_start_cycle_end", None) if h else None


def _has_data(db: Session, household_id: int, cycle_end: str) -> bool:
    """Did we actually record this cycle? Absence of spend is not evidence of thrift."""
    from datetime import timedelta

    from models.transaction import Transaction
    from services.cycle import cycle_bounds, household_start_day

    end_d = date.fromisoformat(cycle_end)
    start, _ = cycle_bounds(end_d, household_start_day(db, household_id))
    n = (db.query(Transaction)
         .filter(Transaction.household_id == household_id,
                 Transaction.archived.is_(False), Transaction.dup_of_id.is_(None),
                 Transaction.date >= start.isoformat(),
                 Transaction.date <= (start + timedelta(days=45)).isoformat())
         .count())
    # A handful of stray rows is not a tracked month either. Ten is low enough to admit a genuinely
    # quiet cycle and high enough to exclude one that was never really captured.
    return n >= 10


def outflows(db: Session, household_id: int) -> list[SurplusDraw]:
    return (db.query(SurplusDraw)
            .filter(SurplusDraw.household_id == household_id)
            .order_by(SurplusDraw.drawn_on.desc()).all())


def balance(db: Session, household_id: int) -> float:
    saved = sum(i["saved"] for i in inflows(db, household_id))
    spent = sum(d.amount for d in outflows(db, household_id))
    return round(saved - spent, 2)


def spend(db: Session, household_id: int, amount: float, on: str | None = None,
          loan_id: int | None = None, transaction_id: int | None = None,
          note: str | None = None, recurring_id: int | None = None) -> SurplusDraw:
    """Take money out of the wallet for a loan payment, an investment, or anything else.

    Refuses to overdraw. A wallet that can go negative is not a balance, it is another figure to
    reconcile — and the whole point of this was to make "what happened to that money" answerable.
    """
    if amount <= 0:
        raise ValueError("amount must be positive")
    left = balance(db, household_id)
    if amount > left + 0.01:
        raise ValueError(
            f"the wallet holds {left:,.2f}, cannot spend {amount:,.2f} — "
            f"pay the difference from another source, or record less")
    d = SurplusDraw(household_id=household_id, amount=round(amount, 2), loan_id=loan_id,
                    recurring_id=recurring_id,
                    transaction_id=transaction_id, note=note, funded_from="wallet",
                    drawn_on=on or date.today().isoformat(), created_at=datetime.utcnow())
    db.add(d)
    db.commit()
    return d


def summary(db: Session, household_id: int) -> dict:
    ins = inflows(db, household_id)
    outs = outflows(db, household_id)
    return {
        "balance": balance(db, household_id),
        "saved_total": round(sum(i["saved"] for i in ins), 2),
        "spent_total": round(sum(d.amount for d in outs), 2),
        "inflows": ins,
        "outflows": [{"id": d.id, "amount": d.amount, "on": d.drawn_on, "loan_id": d.loan_id,
                      "recurring_id": d.recurring_id,
                      "transaction_id": d.transaction_id, "note": d.note,
                      "funded_from": d.funded_from} for d in outs],
        "starts_from": _start_cycle(db, household_id),
        "note": "Money you already hold — already counted in net worth, never added to it.",
    }
