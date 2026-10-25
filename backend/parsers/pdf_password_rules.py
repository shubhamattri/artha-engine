"""Derive statement PDF passwords instead of storing them.

A fixed list of passwords does not scale: it needs a human to add an entry for every new card, and it
silently fails when a bank changes format. The banks themselves publish the rule in the very email
that carries the statement, and every rule is a function of details the household already knows. So
we generate candidates from a profile and try those.

Rules below are quoted from the emails actually received:

  Kotak      "If your name is Raj Bhargava and date of birth is 28-08-1992, your password is
              rajb2808"                                     -> first4(name).lower() + DDMM
  SBI        "the last five digits of customer registered mobile number and date of birth (DOB) in
              DDMMYY format"                                -> mobile[-5:] + DDMMYY
  CDSL eCAS  "The password is PAN (in capital) of the first holder"
  CAMS       "In case PAN not available, first 4 letters of Name + last four letters of PINCODE"

The HDFC/ICICI/Axis card convention (first four of the name in caps + DDMM) is not spelled out in
their mails but is the long-standing published format, so it is included as a candidate rather than
an assertion — a wrong candidate simply fails to open the file.

Nothing here is logged. Candidates are built in memory, tried, and discarded.
"""

from __future__ import annotations

import re
from datetime import date


def _digits(s: str | None) -> str:
    return re.sub(r"\D", "", s or "")


def _name4(name: str | None) -> list[str]:
    """First four letters of the name, both readings banks use.

    Kotak's own worked example is "Raj Bhargava" -> "rajb", i.e. letters of the WHOLE name with
    spaces removed. Others use the first name alone ("Aarushi" -> "aaru"). Both are returned because
    a wrong candidate costs nothing but a failed open, while missing the right one costs the unlock.
    """
    if not name:
        return []
    whole = re.sub(r"[^A-Za-z]", "", name)[:4]
    first = re.sub(r"[^A-Za-z]", "", (name.strip().split() or [""])[0])[:4]
    out = [x for x in (whole, first) if len(x) == 4]
    seen: set[str] = set()
    return [x for x in out if not (x in seen or seen.add(x))]


def candidates(name: str | None = None, dob: date | str | None = None,
               mobile: str | None = None, pan: str | None = None,
               pincode: str | None = None, last4: str | None = None) -> list[str]:
    """Every password the household's own details could produce, most-likely first.

    Order matters only for speed — a wrong candidate just fails to open the PDF, so a longer list
    costs a few milliseconds, never correctness. Returns [] when nothing is known, which keeps the
    caller's behaviour identical to before this module existed.
    """
    out: list[str] = []

    d: date | None = None
    if isinstance(dob, date):
        d = dob
    elif isinstance(dob, str) and dob.strip():
        s = dob.strip()
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d%m%Y"):
            try:
                from datetime import datetime
                d = datetime.strptime(s, fmt).date()
                break
            except ValueError:
                continue

    names4 = _name4(name)
    mob = _digits(mobile)
    pin = _digits(pincode)
    pan_u = (pan or "").strip().upper()

    ddmm = f"{d.day:02d}{d.month:02d}" if d else ""
    ddmmyy = f"{ddmm}{d.year % 100:02d}" if d else ""
    ddmmyyyy = f"{ddmm}{d.year}" if d else ""

    for n4 in names4:
        if ddmm:
            out += [f"{n4.lower()}{ddmm}", f"{n4.upper()}{ddmm}", f"{n4.capitalize()}{ddmm}"]
    if mob and ddmmyy:
        out.append(f"{mob[-5:]}{ddmmyy}")                 # SBI, as quoted
    if pan_u:
        out += [pan_u, pan_u.lower()]
    for n4 in names4:
        if pin:
            out.append(f"{n4.upper()}{pin[-4:]}")         # CAMS fallback, as quoted
    if ddmmyyyy:
        out.append(ddmmyyyy)
    if last4 and ddmm:
        out += [f"{last4}{ddmm}", f"{ddmm}{last4}"]
    for n4 in names4:
        if last4:
            out.append(f"{n4.upper()}{last4}")

    # de-dup, keep order
    seen: set[str] = set()
    return [c for c in out if c and not (c in seen or seen.add(c))]


def candidates_from_settings(last4: str | None = None) -> list[str]:
    """Build candidates from the profile in .env — the same gitignored place the static passwords
    already live, so no new home for PII is introduced."""
    try:
        from config import settings
    except Exception:  # noqa: BLE001
        return []
    return candidates(
        name=getattr(settings, "statement_holder_name", "") or None,
        dob=getattr(settings, "statement_dob", "") or None,
        mobile=getattr(settings, "statement_mobile", "") or None,
        pan=getattr(settings, "statement_pan", "") or None,
        pincode=getattr(settings, "statement_pincode", "") or None,
        last4=last4,
    )


def candidates_for_card(db, last4: str | None, household_id: int | None = None) -> list[str]:
    """Passwords for a specific card, using the details of the member who HOLDS it.

    A household is not one person. A card in one spouse's name is locked with that spouse's date of
    birth, so deriving from a single household-wide profile would fail for every card the other
    person holds. The account registry already records the holder, so use it: card -> holder ->
    that member's identity.

    Falls back to every member in the household when the holder is unknown (an unregistered card, or
    one added without picking a holder) — trying a few extra candidates costs milliseconds, and the
    alternative is failing to open a statement we could have opened.
    """
    out: list[str] = []
    try:
        from models.account import Account
        from models.tenant import Member
    except Exception:  # noqa: BLE001
        return []

    holder = None
    if last4:
        q = db.query(Account).filter(Account.last4 == last4, Account.active.is_(True))
        if household_id is not None:
            q = q.filter(Account.household_id == household_id)
        acct = q.first()
        holder = acct.holder if acct else None

    mq = db.query(Member).filter(Member.active.is_(True), Member.is_household.is_(False))
    if household_id is not None:
        mq = mq.filter(Member.household_id == household_id)
    members = mq.all()
    if holder:
        matched = [m for m in members if (m.key or m.name) == holder]
        members = matched or members       # unknown holder -> try everyone

    for m in members:
        out += candidates(name=m.name, dob=m.dob, mobile=m.mobile,
                          pan=m.pan, pincode=m.pincode, last4=last4)

    seen: set[str] = set()
    return [c for c in out if c and not (c in seen or seen.add(c))]
