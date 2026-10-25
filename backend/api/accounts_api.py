"""Accounts & cards CRUD, plus the unknown-sender inbox.

The inbox is the point of this module. Artha already SEES every bank-like SMS sender on the Mac; it
just silently drops the ones it doesn't recognise. That is how a new credit card goes missing for
weeks with no error anywhere. Surfacing the unmatched senders turns a silent gap into a prompt.
"""

from __future__ import annotations

import re

from api.deps import get_current_household
from database import get_db
from fastapi import APIRouter, Depends, HTTPException
from models.account import Account
from models.tenant import Household
from pydantic import BaseModel
from sqlalchemy.orm import Session

router = APIRouter()


class AccountIn(BaseModel):
    bank: str
    kind: str = "card"
    last4: str
    holder: str | None = None
    label: str | None = None
    sms_senders: str | None = None
    active: bool = True


def _clean(body: AccountIn) -> AccountIn:
    body.last4 = (body.last4 or "").strip()[-4:]
    if not body.last4.isdigit() or len(body.last4) != 4:
        raise HTTPException(400, "last4 must be exactly 4 digits")
    if body.kind not in ("card", "account"):
        raise HTTPException(400, "kind must be 'card' or 'account'")
    if not (body.bank or "").strip():
        raise HTTPException(400, "bank is required")
    body.bank = body.bank.strip()
    return body


def _row(a: Account) -> dict:
    row = {"id": a.id, "bank": a.bank, "kind": a.kind, "last4": a.last4, "holder": a.holder,
           "label": a.label, "sms_senders": a.sms_senders, "active": a.active,
           "statement_sender": a.statement_sender, "statement_day": a.statement_day,
           "statement_run_day": a.statement_run_day,
           "statement_subject_hint": a.statement_subject_hint,
           "statement_last_imported": a.statement_last_imported,
           "cycle_start": None, "cycle_end": None, "cycle_days_left": None}
    # The card's OWN billing window, which is not the household budget cycle and can differ from it
    # by a day or more. Published so a card view can answer "will this purchase be on this bill?"
    # without anyone re-deriving the rule and getting the boundary a day out.
    if a.statement_day:
        from datetime import date

        from services.statement_schedule import card_cycle_bounds
        start, end = card_cycle_bounds(a.statement_day, date.today())
        row.update(cycle_start=start.isoformat(), cycle_end=end.isoformat(),
                   cycle_days_left=(end - date.today()).days)
    return row


@router.get("/accounts")
def list_accounts(db: Session = Depends(get_db),
                  household: Household = Depends(get_current_household)) -> dict:
    rows = (db.query(Account).filter(Account.household_id == household.id)
            .order_by(Account.bank, Account.kind, Account.last4).all())
    return {"accounts": [_row(a) for a in rows]}


@router.post("/accounts")
def create_account(body: AccountIn, db: Session = Depends(get_db),
                   household: Household = Depends(get_current_household)) -> dict:
    body = _clean(body)
    dupe = (db.query(Account)
            .filter(Account.household_id == household.id, Account.last4 == body.last4,
                    Account.bank == body.bank).first())
    if dupe:
        raise HTTPException(400, f"{body.bank} •{body.last4} is already registered")
    a = Account(household_id=household.id, **body.model_dump())
    db.add(a)
    db.commit()
    return _row(a)


@router.patch("/accounts/{account_id}")
def update_account(account_id: int, body: AccountIn, db: Session = Depends(get_db),
                   household: Household = Depends(get_current_household)) -> dict:
    a = (db.query(Account)
         .filter(Account.id == account_id, Account.household_id == household.id).first())
    if a is None:
        raise HTTPException(404, "No such account")
    body = _clean(body)
    for k, v in body.model_dump().items():
        setattr(a, k, v)
    db.commit()
    return _row(a)


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int, db: Session = Depends(get_db),
                   household: Household = Depends(get_current_household)) -> dict:
    a = (db.query(Account)
         .filter(Account.id == account_id, Account.household_id == household.id).first())
    if a is None:
        raise HTTPException(404, "No such account")
    db.delete(a)
    db.commit()
    return {"deleted": account_id}


# Sender headers that are bank-shaped but carry no transaction — never offer these as an account.
_NOISE = ("OTP", "VERIFY", "AUTH")


@router.get("/accounts/unknown-senders")
def unknown_senders(days: int = 90, db: Session = Depends(get_db),
                    household: Household = Depends(get_current_household)) -> dict:
    """Bank-like SMS senders on this Mac that no registered account claims.

    Each row is a candidate the user can turn into an account in one click, which is the self-service
    version of editing the hardcoded bank list. Returns an empty list (never an error) when chat.db
    is unreadable, so the UI degrades quietly on a machine without Full Disk Access.
    """
    try:
        from services.sms_capture import scan_all_senders
        seen = scan_all_senders(since_days=days)
    except Exception:  # noqa: BLE001 — no FDA, no Messages, non-Mac host
        return {"unknown": [], "available": False}

    known = [s.strip().upper()
             for a in db.query(Account).filter(Account.household_id == household.id).all()
             for s in (a.sms_senders or "").split(",") if s.strip()]

    out = []
    for row in seen:
        up = (row["sender"] or "").upper()
        if any(n in up for n in _NOISE):
            continue
        if any(k in up for k in known):
            continue
        # Only senders that have actually reported a debit or credit. Without this the list runs to
        # ~200 rows of delivery updates and marketing, which is worse than showing nothing: a prompt
        # you have to sift is a prompt you ignore.
        if not row.get("transactional"):
            continue
        # Strip the telecom prefix/suffix (VD-BOBCRD-S -> BOBCRD) to suggest a usable sender token.
        core = re.sub(r"^[A-Z]{2}-|-[A-Z]$", "", up)
        out.append({"sender": row["sender"], "suggested_token": core,
                    "count": row["count"], "last_seen": row["last_seen"],
                    "sample": row.get("sample") or ""})
    out.sort(key=lambda r: (-r["count"], r["sender"]))
    return {"unknown": out, "available": True}


@router.get("/accounts/statement-schedule/discover")
def discover_schedule(days: int = 190, db: Session = Depends(get_db),
                      household: Household = Depends(get_current_household)) -> dict:
    """When each statement sender actually mails, derived from the mailbox.

    The schedule is discovered rather than typed because nobody knows offhand that their HDFC card
    statement lands on the 22nd — but the mailbox does, and it has months of evidence.
    """
    try:
        from services.statement_schedule import discover
        return {"available": True, "senders": discover(days=days)}
    except Exception as exc:  # noqa: BLE001 — Gmail not connected on this machine
        return {"available": False, "senders": [], "reason": str(exc)[:120]}


class ScheduleIn(BaseModel):
    statement_sender: str | None = None
    statement_day: int | None = None
    statement_run_day: int | None = None


@router.patch("/accounts/{account_id}/statement-schedule")
def set_schedule(account_id: int, body: ScheduleIn, db: Session = Depends(get_db),
                 household: Household = Depends(get_current_household)) -> dict:
    """Point an account at the sender that mails its statement, and when to go looking."""
    a = (db.query(Account)
         .filter(Account.id == account_id, Account.household_id == household.id).first())
    if a is None:
        raise HTTPException(404, "No such account")
    for field in ("statement_day", "statement_run_day"):
        v = getattr(body, field)
        if v is not None and not (1 <= v <= 28):
            # 28 so a schedule never silently skips February
            raise HTTPException(400, f"{field} must be between 1 and 28")
    if body.statement_sender is not None:
        a.statement_sender = body.statement_sender.strip().lower() or None
    if body.statement_day is not None:
        a.statement_day = body.statement_day
        if a.statement_run_day is None:
            a.statement_run_day = min(28, body.statement_day + 1)
    if body.statement_run_day is not None:
        a.statement_run_day = body.statement_run_day
    db.commit()
    return {"id": a.id, "statement_sender": a.statement_sender, "statement_day": a.statement_day,
            "statement_run_day": a.statement_run_day,
            "statement_last_imported": a.statement_last_imported}
