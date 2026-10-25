"""Fetch every scheduled card's outstanding statements now, ignoring the run day.

    cd backend && .venv/bin/python -m tools.backfill_statements            # show what is missing
    cd backend && .venv/bin/python -m tools.backfill_statements --apply    # fetch and ingest

due_today() answers "should today's job fetch this card", and its catch-up only reaches back to the
CURRENT month's run day. A card whose statement was generated last month and never imported is
therefore invisible until this month's run day arrives -- Amazon Pay, Axis and three HDFC cards all
sat at "never imported" with nothing due, and would have stayed that way for up to eleven days.

Kept separate from the schedule on purpose. Making due_today() ignore the run day would fix this
case and create a worse one: a card whose bank genuinely issued no statement for a month would never
stamp, stay due forever, and re-read its mailbox every ten minutes. The schedule stays for the
steady state; catching up is a thing you ask for.
"""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="fetch and ingest (default: show only)")
    ap.add_argument("--household", type=int, default=1)
    args = ap.parse_args()

    sys.path.insert(0, "../scripts")
    from daily_sync import _ingest_for_account
    from database import SessionLocal
    from models.account import Account

    db = SessionLocal()
    cards = (db.query(Account)
             .filter(Account.household_id == args.household, Account.active.is_(True),
                     Account.kind == "card", Account.statement_sender.isnot(None))
             .order_by(Account.statement_day).all())

    print(f"{len(cards)} scheduled card(s)")
    for a in cards:
        print(f"  {a.bank} {a.last4} {a.label or '':16.16} last imported: "
              f"{a.statement_last_imported or 'NEVER'}")

    if not args.apply:
        print("\nShowing only. Re-run with --apply to fetch.")
        return 0

    total = {"fetched": 0, "ingested": 0, "added": 0}
    for a in cards:
        res = {"fetched": 0, "card_statements": 0, "ingested": 0, "added": 0, "locked": 0,
               "unsupported": 0, "errors": []}
        _ingest_for_account(db, a, res)
        for k in total:
            total[k] += res.get(k, 0)
        flag = f"  ERRORS {res['errors'][:2]}" if res["errors"] else ""
        print(f"  {a.bank} {a.last4:6} fetched {res['fetched']:>2}  ingested {res['ingested']:>2}  "
              f"rows +{res['added']:<4} now: {a.statement_last_imported or 'NEVER'}{flag}")
    print(f"\ntotal: {total['fetched']} fetched, {total['ingested']} ingested, "
          f"{total['added']} rows added")
    return 0


if __name__ == "__main__":
    sys.exit(main())
