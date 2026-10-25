"""Product config + household members — kills hardcoded brand/holder strings in the UI.

  GET /config   (public)  → brand + currency + locale, so the frontend renders no literals.
  GET /members  (auth)    → the current household's members (holder pickers come from here).
"""

from __future__ import annotations

import re
from datetime import date, datetime

from api.deps import get_current_household
from config import settings
from database import get_db
from fastapi import APIRouter, Body, Depends, HTTPException
from models.tenant import Household, Member
from pydantic import BaseModel
from sqlalchemy.orm import Session

router = APIRouter()


@router.get("/config")
def get_config():
    """Branding and formatting only. Genuinely public: the login screen renders the wordmark before
    any session exists, so this cannot require one.

    It used to also return the billing cycle and the per-card windows, read from the GLOBAL settings
    with a comment conceding "public route: no household yet". That was not merely un-scoped, it was
    wrong: a household that sets its own `cycle_start_day` still saw the app default here, which is
    how the Spending tab came to say "the 12th to the 12th" for a household on the 13th. Those three
    fields now come from /household/cycle, which knows whose cycle it is.
    """
    return {
        "brand_name": settings.brand_name,
        "brand_wordmark": settings.brand_wordmark,
        "currency": settings.currency,
        "currency_symbol": settings.currency_symbol,
        "locale": settings.locale,
    }


@router.get("/household/cycle")
def get_household_cycle(db: Session = Depends(get_db),
                        household: Household = Depends(get_current_household)):
    """This household's billing-cycle anchor day, and the window it currently implies."""
    from datetime import date

    from api.annual import household_year_start, year_containing, year_label
    from services.cycle import cycle_bounds, household_start_day

    day = household_start_day(db, household.id)
    start, end = cycle_bounds(date.today(), day)
    ysm = household_year_start(db, household.id)
    # How this household's cycle is windowed, so the UI can DESCRIBE it instead of hardcoding bank
    # names and dates in copy. Read per household, not from the app defaults.
    from services.cycle import card_cycle_config
    per_card, card_days, _ = card_cycle_config(db, household.id)

    return {"cycle_start_day": day, "is_default": household.cycle_start_day is None,
            "start": start.isoformat(), "end_exclusive": end.isoformat(),
            "days": (end - start).days,
            "per_card_cycles": per_card,
            "card_cycle_days": card_days if per_card else {},
            "year_start_month": ysm,
            "year_is_default": household.year_start_month is None,
            "current_year": year_label(year_containing(date.today(), ysm), ysm)}


@router.put("/household/cycle")
def set_household_cycle(payload: dict = Body(...), db: Session = Depends(get_db),
                        household: Household = Depends(get_current_household)):
    """Set this household's cycle anchor day (1–31). A day past a short month's end clamps to that
    month's last day, so 31 is a legitimate choice for a card that bills month-end."""
    raw = payload.get("cycle_start_day")
    if raw is None:
        household.cycle_start_day = None                 # fall back to the global default
    else:
        try:
            day = int(raw)
        except (TypeError, ValueError):
            raise HTTPException(400, "cycle_start_day must be a whole number between 1 and 31")
        if not 1 <= day <= 31:
            raise HTTPException(400, "cycle_start_day must be between 1 and 31")
        household.cycle_start_day = day

    # Only touched when the key is PRESENT, so a caller setting the cycle day cannot silently reset
    # the financial year it never mentioned.
    if "year_start_month" in payload:
        raw = payload.get("year_start_month")
        if raw is None:
            household.year_start_month = None            # fall back to April
        else:
            try:
                month = int(raw)
            except (TypeError, ValueError):
                raise HTTPException(400, "year_start_month must be a whole number between 1 and 12")
            if not 1 <= month <= 12:
                raise HTTPException(400, "year_start_month must be between 1 and 12")
            household.year_start_month = month
    db.commit()
    # The resolver memoises per session and this request just invalidated its assumption. Without
    # this the response below reports the OLD day and the save reads as having failed.
    from services.cycle import invalidate
    invalidate(db, household.id)
    return get_household_cycle(db=db, household=household)


class HouseholdSettingsIn(BaseModel):
    """Three household-wide settings. Every field optional AND nullable, so the handler has to tell
    "not sent" from "sent as null" — otherwise saving one setting silently wipes the other two."""
    tax_regime: str | None = None            # "old" | "new" | null (null = app default)
    budget_baseline_from: str | None = None  # YYYY-MM-DD | null
    currency: str | None = None              # ISO code | null (null = settings.currency)


def _settings_view(household: Household) -> dict:
    from services.tax import REGIME_NEW

    baseline = household.budget_baseline_from
    return {
        # the stored choice (null = never chosen) alongside what the engines actually use, so the UI
        # can show "not set — defaulting to X" instead of pretending a default was a decision
        "tax_regime": household.tax_regime,
        "tax_regime_effective": household.tax_regime or REGIME_NEW,
        "budget_baseline_from": baseline.isoformat() if baseline else None,
        "currency": household.currency,
        "currency_effective": household.currency or settings.currency,
    }


@router.get("/household/settings")
def get_household_settings(db: Session = Depends(get_db),
                           household: Household = Depends(get_current_household)):
    """Household-wide settings a person must be able to change in the app, not by running a script."""
    return _settings_view(household)


@router.put("/household/settings")
def set_household_settings(body: HouseholdSettingsIn, db: Session = Depends(get_db),
                           household: Household = Depends(get_current_household)):
    """Set tax regime, tracking-start date, and currency. Omitted fields are left alone; null clears
    one back to the app default."""
    from services.tax import REGIME_NEW, REGIME_OLD

    sent = body.model_fields_set

    if "tax_regime" in sent:
        raw = (body.tax_regime or "").strip().lower()
        if raw == "":
            household.tax_regime = None
        elif raw not in (REGIME_OLD, REGIME_NEW):
            raise HTTPException(400, 'tax_regime must be "old" or "new"')
        else:
            household.tax_regime = raw

    if "budget_baseline_from" in sent:
        raw = (body.budget_baseline_from or "").strip()
        if raw == "":
            household.budget_baseline_from = None
        else:
            parsed = None
            for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
                try:
                    parsed = datetime.strptime(raw, fmt).date()
                    break
                except ValueError:
                    continue
            if parsed is None:
                raise HTTPException(400, "Tracking start date must be YYYY-MM-DD")
            # A future date would exclude every period there is, leaving the engine with no history
            # at all and no way for the household to see why its targets went blank.
            if parsed > date.today():
                raise HTTPException(400, "Tracking start date cannot be in the future")
            household.budget_baseline_from = parsed

    if "currency" in sent:
        raw = (body.currency or "").strip().upper()
        if raw == "":
            household.currency = None
        elif not re.fullmatch(r"[A-Z]{3}", raw):
            raise HTTPException(400, "Currency must be a 3-letter code like INR")
        else:
            household.currency = raw

    db.commit()
    return _settings_view(household)


@router.get("/members")
def list_members(db: Session = Depends(get_db), household: Household = Depends(get_current_household)):
    rows = (
        db.query(Member)
        .filter(Member.household_id == household.id, Member.active.is_(True))
        .order_by(Member.is_household, Member.id)
        .all()
    )
    # One source for why each field is asked for. api/profile.py owns the strings; this endpoint
    # feeds the same form for OTHER members, and when the sentences lived in the component too, the
    # two screens ended up describing the same field differently.
    from api.profile import UNLOCKS

    return {
        "household": {"id": household.id, "name": household.name},
        "unlocks": UNLOCKS,
        "members": [
            {
                "id": m.id, "name": m.name, "key": m.key, "is_household": m.is_household,
                # PII is WRITE-ONLY. The UI needs to know whether a value is set (to prompt for it),
                # never what it is — a PAN or mobile that leaves the server ends up in a browser
                # cache, a screenshot or a support transcript. Only the flags come back.
                "has_dob": m.dob is not None,
                "has_mobile": bool(m.mobile),
                "has_pan": bool(m.pan),
                "has_pincode": bool(m.pincode),
                "identity_complete": all([m.dob, m.mobile, m.pan, m.pincode]),
                # Only DATE OF BIRTH blocks. Measured, not assumed: with DOB alone, 23 of 23 real
                # card statements in the mailbox unlocked (HDFC, Axis, Kotak, ICICI/Amazon Pay), because
                # every card issuer's rule is first-four-of-name + DDMM. Mobile is needed only for SBI
                # account statements, PAN only for demat/mutual-fund. Requiring those would have blocked
                # onboarding on details that unlock nothing the household actually has.
                "identity_missing_required": [k for k, v in (("dob", m.dob),) if not v],
                # Prompted, never blocking, with what each one buys.
                "identity_optional_missing": [
                    k for k, v in (("mobile", m.mobile), ("pan", m.pan), ("pincode", m.pincode)) if not v
                ],
            }
            for m in rows
        ],
    }


class MemberIdentityIn(BaseModel):
    """Details banks derive statement passwords from. Every field optional — a household can fill in
    what it has, and each one added unlocks more statements."""
    dob: str | None = None            # YYYY-MM-DD
    mobile: str | None = None
    pan: str | None = None
    pincode: str | None = None


@router.patch("/members/{member_id}/identity")
def update_member_identity(member_id: int, body: MemberIdentityIn,
                           db: Session = Depends(get_db),
                           household: Household = Depends(get_current_household)):
    """Set the identity used to unlock a member's statement PDFs.

    Values are stored and never returned. Sending null leaves a field unchanged; sending "" clears it.

    HOUSEHOLD-scoped, deliberately not self-only. Any signed-in member can write another member's
    date of birth or PAN, which is a genuine privacy question — those fields unlock statement PDFs,
    and an overwrite breaks someone else's imports with no visible cause.

    It stays open because the product asks for it: onboarding collects these for EVERY member so one
    person can set the household up, and the design says "fill these in on their behalf, or send them
    a link". Closing it to self-only was tried and would have broken that flow — one
    member could no longer unlock another's statements without them signing in.

    Whether an admin should be able to do this is a PERMISSION question, and it belongs to the RBAC
    work, not to tenancy. Recorded there as an open decision rather than settled here by accident.
    """
    m = (db.query(Member)
         .filter(Member.id == member_id, Member.household_id == household.id).first())
    if m is None:
        raise HTTPException(status_code=404, detail="No such member")

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
                raise HTTPException(status_code=400, detail="Date of birth must be YYYY-MM-DD")
            m.dob = parsed
    if body.mobile is not None:
        digits = re.sub(r"\D", "", body.mobile)
        if body.mobile.strip() == "":
            m.mobile = None
        elif len(digits) < 10:
            raise HTTPException(status_code=400, detail="Mobile must have at least 10 digits")
        else:
            m.mobile = digits[-10:]
    if body.pan is not None:
        pan = body.pan.strip().upper()
        if pan == "":
            m.pan = None
        elif not re.fullmatch(r"[A-Z]{5}[0-9]{4}[A-Z]", pan):
            raise HTTPException(status_code=400, detail="PAN must look like ABCDE1234F")
        else:
            m.pan = pan
    if body.pincode is not None:
        pin = re.sub(r"\D", "", body.pincode)
        if body.pincode.strip() == "":
            m.pincode = None
        elif len(pin) != 6:
            raise HTTPException(status_code=400, detail="PIN code must be 6 digits")
        else:
            m.pincode = pin

    db.commit()
    return {"id": m.id, "name": m.name,
            "has_dob": m.dob is not None, "has_mobile": bool(m.mobile),
            "has_pan": bool(m.pan), "has_pincode": bool(m.pincode),
            "identity_complete": all([m.dob, m.mobile, m.pan, m.pincode])}


@router.get("/consistency")
def get_consistency(db: Session = Depends(get_db),
                    household: Household = Depends(get_current_household)):
    """Cross-module invariants. Modules built in separate tabs drift apart silently; this is where
    that becomes visible instead of showing up as a wrong number in a plan."""
    from services.consistency import run_all
    return run_all(db, household.id)
