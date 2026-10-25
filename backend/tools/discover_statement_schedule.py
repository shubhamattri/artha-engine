"""Learn each card's statement date from the mailbox and write it onto the accounts.

    cd backend && .venv/bin/python -m tools.discover_statement_schedule            # show, change nothing
    cd backend && .venv/bin/python -m tools.discover_statement_schedule --apply    # write it

The columns existed and every one of them was NULL, so nothing was scheduled and the sync re-read the
whole mailbox every ten minutes instead. Typing the dates in by hand would rot the first time a bank
moved a cycle; reading them from the mail that already arrived does not.

Prints unmatched cards and unstable senders rather than dropping them. A short, confident list looks
like everything was found, which is exactly the failure this is meant to surface.
"""

from __future__ import annotations

import argparse
import sys


def _resolve(shape: str, candidates: list) -> object | None:
    """Pick which of a bank's cards a subject shape belongs to.

    Only needed when one address carries several cards. ICICI names the card in the subject
    ("Amazon Pay ICICI Bank Credit Card Statement"), and the account carries the same name in its
    label, so the label's distinctive words appearing in the shape is the link. Returns None when
    that is ambiguous, because guessing files one card's spend under another.
    """
    scored = []
    for a in candidates:
        words = [w for w in (a.label or "").lower().split() if len(w) > 2]
        if words and all(w in shape for w in words):
            scored.append((len(words), a))
    if len(scored) == 1:
        return scored[0][1]
    if len(scored) > 1:                       # most specific label wins, ties are ambiguous
        scored.sort(key=lambda t: -t[0])
        if scored[0][0] > scored[1][0]:
            return scored[0][1]
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the schedule (default: show only)")
    ap.add_argument("--household", type=int, default=1)
    args = ap.parse_args()

    from database import SessionLocal
    from models.account import Account
    from services.statement_email import card_issuer
    from services.statement_schedule import discover

    db = SessionLocal()
    cards = (db.query(Account)
             .filter(Account.household_id == args.household, Account.active.is_(True),
                     Account.kind == "card").all())
    print(f"{len(cards)} active card(s)\n")

    # Most recently active sender first, so when a bank has migrated domains and both addresses
    # still show a stable rhythm, the live one claims the card and the retired one is skipped.
    groups = [g for g in discover() if g["looks_like_card"] and g["stable"]]
    groups.sort(key=lambda g: g["last_seen"], reverse=True)
    if not groups:
        print("No stable card sender found. Nothing written.")
        return 2

    plan: dict = {}
    deferred: list = []
    for g in groups:
        issuer = card_issuer(g["sender"])
        bank = issuer[0] if issuer else None
        mine = [c for c in cards if bank and c.bank.lower().startswith(bank.lower()[:4])]
        if not mine:
            continue
        shapes_for_bank = [x for x in groups if (card_issuer(x["sender"]) or [None])[0] == bank]
        if len(mine) > 1 and len({s["shape"] for s in shapes_for_bank}) > 1:
            # several cards AND several subject shapes: the shape must identify one card
            target = _resolve(g["shape"], mine)
            if target is None:
                deferred.append((g, mine))     # may still be settled by elimination below
                continue
            plan.setdefault(target.id, (g, g["shape"]))
        else:
            for c in mine:                     # one schedule, every card on it (HDFC mails all on 22)
                plan.setdefault(c.id, (g, None))

    # Elimination. Only one ICICI subject names its card ("Amazon Pay ICICI Bank Credit Card
    # Statement"); the other is generic, so no label can match it. But once every other card on that
    # sender is placed, one card and one schedule are left over and there is nothing else either can
    # be. Applied only when exactly one of each remains — anything looser is a guess, and guessing
    # files one card's spend under another.
    for g, mine in deferred:
        left = [c for c in mine if c.id not in plan]
        if len(left) == 1:
            plan[left[0].id] = (g, g["shape"])
            print(f"  by elimination: {left[0].bank} {left[0].last4} "
                  f"({left[0].label}) is the only card left on {g['sender']} day {g['day']}")
        else:
            print(f"  ambiguous: {g['sender']} day {g['day']} — {len(left)} cards still "
                  f"unplaced, cannot tell which. Left alone.")

    by_id = {c.id: c for c in cards}
    print(f"{'CARD':28} {'DAY':>4} {'RUN':>4}  SENDER / subject discriminator")
    for cid, (g, hint) in sorted(plan.items()):
        c = by_id[cid]
        print(f"  {c.bank} {c.last4} {(c.label or ''):14.14} {g['day']:>4} {g['run_day']:>4}  "
              f"{g['sender'][:38]}  last seen {g['last_seen']}"
              f"{'  [' + hint[:26] + ']' if hint else ''}")

    missing = [c for c in cards if c.id not in plan]
    for c in missing:
        print(f"  UNMATCHED  {c.bank} {c.last4} {c.label or ''} — no stable card sender found; "
              f"its statement will never be fetched on a schedule")

    if not args.apply:
        print("\nShowing only. Re-run with --apply to write.")
        return 0

    for cid, (g, hint) in plan.items():
        c = by_id[cid]
        c.statement_sender, c.statement_day = g["sender"], g["day"]
        c.statement_run_day, c.statement_subject_hint = g["run_day"], hint
    db.commit()
    print(f"\nWrote schedule for {len(plan)} card(s). {len(missing)} still unscheduled.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
