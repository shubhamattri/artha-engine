"""One profile per person: login, identity, and what they hold.

Before this, a human existed as two disconnected halves — a `users` row that could sign in and a
`members` row that held cards and a date of birth — and the UI had to stitch them together by
guessing which member the signed-in user was. Onboarding wrote to a record the user had no claim on.

This module is the single place that owns a person. Everything about them is read and written here,
from the database, so nothing about a household lives in source.
"""

from __future__ import annotations

import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.deps import get_current_household, get_current_user
from database import get_db
from models.tenant import Household, Member
from models.user import User

router = APIRouter()

# Only date of birth blocks. Measured on the real mailbox: with DOB alone every card statement
# unlocked, because each issuer's rule is first-four-of-name + DDMM. Mobile matters only for SBI
# account statements and PAN only for demat/mutual-fund, so requiring them would block onboarding on
# details that unlock nothing most households have.
REQUIRED = ("dob",)
OPTIONAL = ("mobile", "pan", "pincode")

# What each field actually buys, surfaced so the UI never asks for personal data without a reason.
# WHY each field is being asked for, which is the only thing that makes asking reasonable. Server
# side because two screens show it — the profile and the household's identity form — and when the
# same sentence lived in both, they drifted into saying different things about the same field.
UNLOCKS = {
    "dob": "Every credit card statement. Banks build the password from your name and this.",
    "mobile": "SBI account statements only. Skip it if you have no SBI account.",
    "pan": "Demat and mutual fund statements (CDSL, CAMS).",
    "pincode": "A fallback some fund houses use when PAN is not on file.",
}


class ProfileIn(BaseModel):
    name: str | None = None
    dob: str | None = None
    mobile: str | None = None
    pan: str | None = None
    pincode: str | None = None


def _ensure_member(db: Session, user: User) -> Member:
    """The person behind this login, creating the record if the link was never made.

    Self-healing rather than an error: a user who signed up before members existed should not be
    stuck on a broken profile screen.
    """
    if user.member_id:
        m = db.query(Member).filter(Member.id == user.member_id).first()
        if m:
            return m
    m = (db.query(Member)
         .filter(Member.household_id == user.household_id, Member.is_household.is_(False))
         .filter((Member.key.ilike(user.name)) | (Member.name.ilike(user.name))).first())
    if m is None:
        m = Member(household_id=user.household_id, name=user.name, key=user.name,
                   is_household=False, active=True)
        db.add(m)
        db.flush()
    user.member_id = m.id
    db.commit()
    return m


def _view(db: Session, user: User, m: Member) -> dict:
    from models.account import Account

    held = (db.query(Account)
            .filter(Account.household_id == user.household_id, Account.active.is_(True))
            .filter(Account.holder.isnot(None))
            .filter((Account.holder == m.key) | (Account.holder == m.name)).all())
    have = {"dob": m.dob is not None, "mobile": bool(m.mobile),
            "pan": bool(m.pan), "pincode": bool(m.pincode)}
    return {
        # login
        "user_id": user.id, "username": user.username, "email": user.email,
        # person
        "member_id": m.id, "name": m.name, "holder_key": m.key,
        # identity — WRITE-ONLY. Whether a value is set, never the value: anything returned here ends
        # up in a browser cache, a screenshot, or a support transcript.
        "identity": {k: {"set": v, "required": k in REQUIRED, "unlocks": UNLOCKS[k]}
                     for k, v in have.items()},
        "missing_required": [k for k in REQUIRED if not have[k]],
        "missing_optional": [k for k in OPTIONAL if not have[k]],
        "complete": all(have[k] for k in REQUIRED),
        # what this person actually holds, so the profile explains why the details are wanted
        "accounts": [{"id": a.id, "bank": a.bank, "kind": a.kind, "last4": a.last4,
                      "label": a.label} for a in held],
    }


@router.get("/profile")
def get_profile(db: Session = Depends(get_db), user: User = Depends(get_current_user),
                household: Household = Depends(get_current_household)) -> dict:
    m = _ensure_member(db, user)
    return {"household": {"id": household.id, "name": household.name}, **_view(db, user, m)}


@router.patch("/profile")
def update_profile(body: ProfileIn, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)) -> dict:
    """Update the signed-in person. Omitted fields are untouched; "" clears one."""
    m = _ensure_member(db, user)

    if body.name is not None and body.name.strip():
        m.name = body.name.strip()
    if body.dob is not None:
        if body.dob.strip() == "":
            m.dob = None
        else:
            parsed = None
            for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
                try:
                    parsed = datetime.strptime(body.dob.strip(), fmt).date()
                    break
                except ValueError:
                    continue
            if parsed is None:
                raise HTTPException(400, "Date of birth must be YYYY-MM-DD")
            m.dob = parsed
    if body.mobile is not None:
        d = re.sub(r"\D", "", body.mobile)
        if body.mobile.strip() == "":
            m.mobile = None
        elif len(d) < 10:
            raise HTTPException(400, "Mobile must have at least 10 digits")
        else:
            m.mobile = d[-10:]
    if body.pan is not None:
        p = body.pan.strip().upper()
        if p == "":
            m.pan = None
        elif not re.fullmatch(r"[A-Z]{5}[0-9]{4}[A-Z]", p):
            raise HTTPException(400, "PAN must look like ABCDE1234F")
        else:
            m.pan = p
    if body.pincode is not None:
        p = re.sub(r"\D", "", body.pincode)
        if body.pincode.strip() == "":
            m.pincode = None
        elif len(p) != 6:
            raise HTTPException(400, "PIN code must be 6 digits")
        else:
            m.pincode = p

    db.commit()
    return _view(db, user, m)


@router.get("/profile/household")
def household_profiles(db: Session = Depends(get_db), user: User = Depends(get_current_user),
                       household: Household = Depends(get_current_household)) -> dict:
    """Everyone in the household and whether their statements can be opened.

    A household is not one person: a card in another member's name is locked with THEIR date of
    birth, so the screen has to show whose details are still missing, not only the signed-in user's.
    """
    _ensure_member(db, user)
    people = (db.query(Member)
              .filter(Member.household_id == household.id, Member.is_household.is_(False),
                      Member.active.is_(True))
              .order_by(Member.id).all())
    linked = {u.member_id: u for u in
              db.query(User).filter(User.household_id == household.id).all() if u.member_id}
    return {
        "household": {"id": household.id, "name": household.name},
        "people": [{
            "member_id": m.id, "name": m.name,
            "has_login": m.id in linked,
            "is_you": m.id == user.member_id,
            "missing_required": [k for k in REQUIRED
                                 if not {"dob": m.dob}.get(k)],
            "missing_optional": [k for k in OPTIONAL
                                 if not {"mobile": m.mobile, "pan": m.pan, "pincode": m.pincode}.get(k)],
            "complete": m.dob is not None,
        } for m in people],
    }
