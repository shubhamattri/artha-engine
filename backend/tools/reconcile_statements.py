"""Reconcile each card's latest statement against what Artha captured.

    cd backend && .venv/bin/python -m tools.reconcile_statements [--card 1234]

The statement is the AUTHORITY. SMS and email alerts are a near-real-time ESTIMATE that can miss a
transaction silently, because nothing raises an error for a message that simply never arrived.

Comparing totals alone would hide that: a missed debit and an unrecorded credit can net out to a
figure that looks right. So this asks both directions.

  MISSING  the bank billed it, Artha never saw it. Real spend absent from every figure.
  EXTRA    Artha holds a row in that window the statement does not list. Usually a pending
           authorisation that never settled, or a capture filed against the wrong card.

Runs through ingest_statement(dry_run=True) rather than parsing directly, so it reconciles against
exactly the path a real import takes. A second parsing implementation here would eventually disagree
with the real one, and the disagreement would look like a reconciliation finding.
"""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--card", help="last4; default: every scheduled card")
    ap.add_argument("--household", type=int, default=1)
    args = ap.parse_args()

    from database import SessionLocal
    from models.account import Account
    from services import gmail_sync
    from services.ingest import ingest_statement
    from services.statement_schedule import subject_shape
    from sqlalchemy import text

    db = SessionLocal()
    q = (db.query(Account)
         .filter(Account.household_id == args.household, Account.active.is_(True),
                 Account.kind == "card", Account.statement_sender.isnot(None)))
    if args.card:
        q = q.filter(Account.last4 == args.card)
    cards = q.order_by(Account.statement_day).all()
    if not cards:
        print("no scheduled cards match")
        return 2

    grand = {"parsed": 0, "missing": 0, "extra": 0}
    for a in cards:
        print(f"\n=== {a.bank} {a.last4} {a.label or ''} ===")
        best = None
        query = f"has:attachment from:{a.statement_sender} newer_than:45d"
        for fn, data, meta in gmail_sync.fetch_statement_attachments(query=query, with_context=True):
            if not (meta or {}).get("bank"):
                continue
            hint = a.statement_subject_hint
            if hint and hint not in subject_shape(meta.get("subject", "")):
                continue
            r = ingest_statement(db, fn, data, bank=meta["bank"], card_type=meta["card_type"],
                                 hint_passwords=meta.get("passwords"), dry_run=True)
            if r.get("error") or not r.get("period_end"):
                continue
            if best is None or r["period_end"] > best["period_end"]:
                best = r
        if best is None:
            print("  no statement retrievable from this sender in the last 45 days")
            continue

        lo, hi = best["period_start"], best["period_end"]
        held = db.execute(text(
            "select count(*) from transactions where card_last4 = :l4 "
            "and date >= :lo and date <= :hi and archived is false and dup_of_id is null"),
            {"l4": a.last4, "lo": lo, "hi": hi}).scalar() or 0
        parsed, missing = best["transactions_parsed"], best["would_insert"]
        # Rows Artha holds for the window that the statement does not account for. Derived from the
        # counts rather than matched row-by-row: the statement's own rows minus the ones Artha was
        # missing is how many of them Artha already had, and anything held beyond that is unbilled.
        # A file named "Current" is the bank's mid-cycle view of an UNBILLED period, not a closed
        # statement. Comparing a full window against a partial snapshot manufactures extras: Amazon
        # Pay showed 18 against a 24-June file that simply stopped before the rest of the cycle.
        partial = "current" in (best.get("filename") or "").lower()
        extra = max(0, held - (parsed - missing))
        grand["parsed"] += parsed
        grand["missing"] += missing
        if not partial:
            grand["extra"] += extra

        if partial:
            verdict = "PARTIAL — unbilled mid-cycle file, extras are not a finding"
        elif missing == 0 and extra == 0:
            verdict = "CLEAN"
        else:
            verdict = "GAP"
        print(f"  statement {lo} -> {hi}: {parsed} rows   (stmt month {best.get('stmt_month')})")
        print(f"  file: {best.get('filename')}")
        print(f"  artha holds {held} rows in the same window")
        print(f"  MISSING {missing}   EXTRA {extra}   -> {verdict}")
        # Statement rows, labelled as such. Printing these under the EXTRA count read as though they
        # WERE the extras, which is the opposite of what they are.
        for smp in best.get("sample", [])[:3]:
            print(f"    statement row  {smp['date']}  {float(smp['amount'] or 0):>10,.2f}  "
                  f"{smp['description'][:40]}")

    print(f"\nTOTAL across {len(cards)} card(s): {grand['parsed']} statement rows, "
          f"{grand['missing']} missing from Artha, {grand['extra']} unbilled extras")
    return 0


if __name__ == "__main__":
    sys.exit(main())
