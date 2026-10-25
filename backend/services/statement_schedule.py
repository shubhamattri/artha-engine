"""Discover each card's statement schedule from the mailbox, and run the monthly audit on it.

Two different jobs get confused with each other, so to be explicit:

  DAILY SYNC       captures transactions as they happen (SMS + email alerts). Near-real-time, runs
                   every 10 minutes, tells you what you spent today.
  MONTHLY AUDIT    fetches the bank's own statement and reconciles the daily capture against it. The
                   statement is the AUTHORITY; the daily capture is an estimate that can miss things.
                   It exists once a month, on a date the bank picks per card.

Running the monthly audit from a nightly cron is the wrong shape: it either runs uselessly 30 times
or misses the one day that mattered. So each card carries its own schedule, and that schedule is
DISCOVERED from the mailbox rather than typed in by hand: look at when the statement actually arrived
over the last few months, take the day it lands on, and run the day after for safety.

Sender is the key, not the card number: banks mask the number in filenames and subjects
("4854XXXXXXXXXX42"), while the from-address never changes.
"""

from __future__ import annotations

import collections
import logging
import re
import statistics
from datetime import date

log = logging.getLogger("artha.statement_schedule")

# A statement sender is a bank mailing a PDF. These substrings are what banks actually use; anything
# else with an attachment is a broker/insurer/retailer and is not a card statement.
_CARD_SENDER_HINTS = ("cards@", "cardstatement", "credit_card", "cc.statements", "emailstatements")
_STATEMENT_HINTS = ("statement", "estatement", "e-statement")

MIN_SAMPLES = 2      # one arrival proves nothing about a monthly rhythm
MAX_SPREAD = 4       # days; wider than this and there is no stable date to key a job to


_MONTHS = ("january february march april may june july august september october november december "
           "jan feb mar apr jun jul aug sep sept oct nov dec").split()


def _addr(from_header: str) -> str:
    return re.sub(r".*<|>.*", "", from_header or "").strip().lower()


def subject_shape(subject: str) -> str:
    """A subject with its dates removed, so the same monthly mail collapses to one stable key.

    Dates must go (they change every month) but masked card numbers must NOT (they are what tells
    two cards apart). Both are digits, so this drops only what looks like a date — month names, the
    1-2 digit day beside them, and 20xx years — and keeps longer runs and anything with an X.

        "ICICI Bank Credit Card Statement for the period March 13 2026 to April 12 2026"
            -> "icici bank credit card statement for the period to"
        "ICICI Bank Credit Card Statement for the period February 3 2026 to March 2 2026"
            -> "icici bank credit card statement for the period to"

    Two shapes, so two schedules, which is the whole point.
    """
    out = []
    # Split on dashes and slashes too. HDFC writes "April-2026" and Kotak "Apr-2026" as ONE token, so
    # splitting on whitespace alone left the month glued to the year, made every month its own shape,
    # and turned 15 samples of one card into 15 series of one — all reported unstable.
    for tok in re.split(r"[\s,\-/]+", (subject or "").lower()):
        tok = tok.strip(".:;()[]-")
        if not tok or tok in _MONTHS:
            continue
        if re.fullmatch(r"\d{1,2}(st|nd|rd|th)?", tok):     # a day of the month
            continue
        if re.fullmatch(r"20\d{2}", tok):                    # a year
            continue
        out.append(tok)
    return " ".join(out)


def discover(days: int = 190, max_messages: int = 200) -> list[dict]:
    """[{sender, day, run_day, samples, spread, stable, subject, looks_like_card}] per sender.

    Deliberately reports UNSTABLE senders too rather than hiding them: "we looked and could not find
    a rhythm" is information the user can act on, whereas a silently short list looks like nothing
    was found.
    """
    from email.utils import parsedate_to_datetime

    from services import gmail_sync

    svc = gmail_sync._service()
    if svc is None:
        return []
    q = f"has:attachment newer_than:{days}d (subject:statement OR subject:e-statement OR subject:card)"
    listing = svc.users().messages().list(userId="me", q=q, maxResults=max_messages).execute()

    by: dict[str, list] = collections.defaultdict(list)
    for ref in listing.get("messages", []):
        msg = svc.users().messages().get(userId="me", id=ref["id"], format="metadata",
                                         metadataHeaders=["From", "Subject", "Date"]).execute()
        h = {x["name"]: x["value"] for x in msg.get("payload", {}).get("headers", [])}
        try:
            when = parsedate_to_datetime(h.get("Date", ""))
        except Exception:  # noqa: BLE001
            continue
        subject = h.get("Subject", "")
        # Keyed on (sender, subject shape), not sender alone. One address can carry two cards on two
        # different dates, and merging them produces a bimodal series that looks unstable.
        by[(_addr(h.get("From", "")), subject_shape(subject))].append((when, subject))

    out: list[dict] = []
    for (sender, shape), items in by.items():
        days_seen = [w.day for w, _ in items]
        spread = max(days_seen) - min(days_seen) if len(days_seen) > 1 else 0
        day = statistics.mode(days_seen)
        stable = len(items) >= MIN_SAMPLES and spread <= MAX_SPREAD
        low = sender.lower()
        subj = items[0][1].lower()
        out.append({
            "sender": sender,
            "shape": shape,
            "day": day,
            # +1 for safety: the mail can land late in the evening, and a statement fetched a day
            # late is fine while one fetched an hour early is simply not there.
            "run_day": min(28, day + 1),
            "samples": len(items),
            # A bank keeps the old address alive for months after migrating, so two senders can look
            # equally valid. The one still receiving mail is the one to fetch from.
            # .date() first: some senders stamp a timezone and some do not, and comparing an aware
            # datetime against a naive one raises. The calendar day is all this needs.
            "last_seen": max(w.date() for w, _ in items).isoformat(),
            "spread": spread,
            "stable": stable,
            "subject": items[0][1][:80],
            "looks_like_card": any(k in low for k in _CARD_SENDER_HINTS)
            or ("card" in subj and any(k in subj for k in _STATEMENT_HINTS)),
        })
    out.sort(key=lambda r: (not r["looks_like_card"], not r["stable"], -r["samples"]))
    return out


def due_today(db, household_id: int, today: date | None = None) -> list:
    """Accounts whose statement should be fetched today.

    Also catches up: an account whose run day has passed this month but whose newest imported
    statement is older than last month is still due, so a missed day (server asleep, no network) does
    not silently skip a whole month.
    """
    from models.account import Account

    today = today or date.today()
    rows = (db.query(Account)
            .filter(Account.household_id == household_id, Account.active.is_(True))
            .filter(Account.statement_sender.isnot(None)).all())
    due = []
    for a in rows:
        run_day = a.statement_run_day or ((a.statement_day + 1) if a.statement_day else None)
        if not run_day:
            continue
        if today.day < min(28, run_day):
            continue
        # the statement that should exist by now is the previous month's
        prev = (today.replace(day=1) - __import__("datetime").timedelta(days=1)).strftime("%Y-%m")
        if (a.statement_last_imported or "") >= prev:
            continue                       # already have it
        due.append(a)
    return due


def card_cycle_bounds(statement_day: int, on: date) -> tuple[date, date]:
    """The billing cycle containing `on`, as (first_day, last_day) inclusive.

    Every card here starts its cycle on the same day its statement arrives, and closes the day
    before: one card's statement lands on the 13th covering "July 13 to August 12". Verified on
    all five cards — Axis's first transactions cluster on the 21st against a statement day of 21,
    and one HDFC card's last transaction is the 21st against a statement day of 22.

    Derived rather than stored. A column would be a second source of truth that can disagree with
    statement_day, and there is no reading that makes the disagreement correct.

    The last day matters for a question with real money on it: a purchase ON the closing day is IN
    the cycle that closes, not the next one. Note the limit though — statements print the
    TRANSACTION date while the bank draws the period on the POSTING date, so a late purchase on the
    closing day can post the next day and land in the following statement.
    """
    from calendar import monthrange

    def _clamp(y: int, m: int, d: int) -> date:
        return date(y, m, min(d, monthrange(y, m)[1]))

    start = _clamp(on.year, on.month, statement_day)
    if on < start:                       # before this month's start, so the cycle began last month
        y, m = (on.year - 1, 12) if on.month == 1 else (on.year, on.month - 1)
        start = _clamp(y, m, statement_day)
    ny, nm = (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
    return start, _clamp(ny, nm, statement_day) - __import__("datetime").timedelta(days=1)
