"""Stage 1: turn an alert into the facts it states. Pure, deterministic, no DB, no LLM.

Every bug this pipeline has had came from guessing a fact the text already contained, then having
nine later passes try to reconstruct it. "Example Bank Acc XX456 debited Rs. 8,000.00 on 05-Aug-26
InfoBIL*Education.Avl Bal Rs. 21,300.50" states the bank, the account, the amount, the date, the
narration and the closing balance. Parsing it to merchant="InfoBIL", amount=8000 threw five of those
away, and the account came out as a phantom "0456" that thirty real transactions were filed under.

Two rules make this safe:

  1. A field the text does not state is None. Never a default, never "0000". A fabricated value is
     worse than a missing one because every later stage believes it.
  2. Masks are kept VERBATIM. "XX456" stays "XX456" here; deciding it means 3456 needs the
     household's accounts, which is Stage 2's job. Normalising early is what created the phantom.

Pure by design so it can be tested exhaustively against real alert text, which is where the bugs
actually live.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date


@dataclass
class Facts:
    """What the alert says. Absent means absent."""

    amount: float | None = None
    direction: str | None = None          # debit | credit
    occurred_on: date | None = None
    account_mask: str | None = None       # verbatim, e.g. "XX456", "4xxx7654"
    counterparty: str | None = None       # VPA, payee name, or merchant as written
    narration: str | None = None          # the bank's own description field
    balance_after: float | None = None
    reference: str | None = None          # UTR / RRN / transaction reference
    kind_hint: str | None = None          # emi_due | bill_paid | transfer | otp | promo
    confidence: str = "none"              # how much of the record was actually parsed

    def as_dict(self) -> dict:
        d = asdict(self)
        d["occurred_on"] = self.occurred_on.isoformat() if self.occurred_on else None
        return d


# ── amount ───────────────────────────────────────────────────────────────────
# "Rs.8,194.00", "INR 68", "Rs 1,50,000.50" (Indian grouping), "₹ 892"
_AMOUNT = re.compile(r"(?:rs\.?|inr|₹)\s*([0-9][0-9,]*(?:\.\d{1,2})?)", re.I)
# "Avl Bal Rs. 45,000.00", "Available Balance INR 1,234", "Total Bal:Rs.90000CR"
_BALANCE = re.compile(
    r"(?:av(?:ai)?l(?:able)?|total|closing)\s*(?:bal(?:ance)?)?[\s:.\-]*"
    r"(?:rs\.?|inr|₹)\s*([0-9][0-9,]*(?:\.\d{1,2})?)", re.I)

_DEBIT = re.compile(r"\b(debited|debit|spent|withdrawn|paid|purchase|deducted)\b", re.I)
_CREDIT = re.compile(r"\b(credited|credit|received|refund(?:ed)?|deposit(?:ed)?)\b", re.I)

# "on 05-Aug-26", "on 02-08-26", "on 2026-08-05", "dated 05/08/2026"
_DATES = [
    (re.compile(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})\b"), "dmy"),
    (re.compile(r"\b(\d{1,2})[-\s]([A-Za-z]{3})[-\s](\d{2,4})\b"), "dMy"),
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), "ymd"),
]
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

# the masked account token, kept verbatim
_ACCOUNT = re.compile(
    r"(?:account|acct|acc|a/c|ac|card)\b\s*(?:no\.?|number|ending(?:\s+in)?)?[\s:.\-]*"
    r"([0-9xX*•.]{3,20})", re.I)

_VPA = re.compile(r"\b([a-z0-9._-]{2,}@[a-z]{2,})\b", re.I)
# "towards VPA x@y (PAYEE NAME)", "to PAYEE NAME on", "at MERCHANT on"
_PAYEE_PARENS = re.compile(r"\(([A-Z][A-Z\s./&-]{3,40})\)")
_REFERENCE = re.compile(
    r"(?:utr|rrn|ref(?:erence)?(?:\s*no\.?)?|txn\s*id|transaction\s*(?:reference|id)\s*no\.?)"
    r"[\s:.\-]*([A-Za-z0-9]{6,25})", re.I)
_IMPS_REF = re.compile(r"\b(?:IMPS|NEFT|UPI)[-\s]([0-9]{9,16})\b", re.I)

_HINTS = [
    ("otp", re.compile(r"\b(otp|one[\s-]time\s*password|do not share)\b", re.I)),
    # The gap between "EMI" and "is due" is real text: "EMI of Rs 30000 for Example Bank Home Loan
    # XX2468 is due on..." runs to ~45 characters, so a 40-char window silently missed every reminder.
    ("emi_due", re.compile(r"\bemi\b.{0,90}?\b(is\s+due|due\s+on)\b|\bdue\s+on\b.{0,40}?\bemi\b", re.I)),
    ("bill_paid", re.compile(r"\b(payment|bill\s*pay(?:ment)?)\b.{0,30}\b(received|processed|successful)\b", re.I)),
    ("transfer", re.compile(r"\b(transfer(?:red)?\s+to|imps|neft|rtgs|fund\s*transfer)\b", re.I)),
    ("promo", re.compile(r"\b(offer|discount|cashback\s+offer|sale|shop\s+now|click\s+here)\b", re.I)),
]

# Banks put the real description after an asterisk: "InfoBIL*Education", "RAZ*Swiggy", "FS*DeHashed".
# Anchoring on the prefix word failed because "BIL" inside "InfoBIL" has no word boundary before it.
# The asterisk itself is the reliable delimiter.
_NARRATION = re.compile(r"\*([A-Za-z][A-Za-z0-9._-]{1,39})")
# A narration runs into the next sentence: "InfoBIL*Education.Avl Bal Rs. 45,000.00". Cut at a period
# that starts a new sentence (followed by a capital), which leaves "DeHashed.com" intact.
_NARRATION_END = re.compile(r"\.(?=[A-Z])")


def _num(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _parse_date(text: str, today: date | None = None) -> date | None:
    """First plausible date in the text. Two-digit years resolve to this century."""
    for rx, kind in _DATES:
        for m in rx.finditer(text):
            try:
                if kind == "ymd":
                    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                elif kind == "dMy":
                    d = int(m.group(1))
                    mo = _MONTHS.get(m.group(2).lower()[:3], 0)
                    y = int(m.group(3))
                else:
                    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if not mo:
                    continue
                if y < 100:
                    y += 2000
                return date(y, mo, d)
            except (ValueError, TypeError):
                continue
    return None


def extract(text: str | None, today: date | None = None) -> Facts:
    """Facts stated by an alert. Anything not stated stays None."""
    f = Facts()
    if not text or not text.strip():
        return f
    t = re.sub(r"\s+", " ", text)

    # Balance is matched FIRST and its span removed, otherwise "Avl Bal Rs. 45,000.00" is read as the
    # transaction amount — a closing balance is not a payment.
    bal = _BALANCE.search(t)
    if bal:
        f.balance_after = _num(bal.group(1))
        t_wo_bal = t[:bal.start()] + " " + t[bal.end():]
    else:
        t_wo_bal = t

    amt = _AMOUNT.search(t_wo_bal)
    if amt:
        f.amount = _num(amt.group(1))

    if _DEBIT.search(t_wo_bal):
        f.direction = "debit"
    elif _CREDIT.search(t_wo_bal):
        f.direction = "credit"

    f.occurred_on = _parse_date(t_wo_bal, today)

    acc = _ACCOUNT.search(t)
    if acc:
        f.account_mask = acc.group(1)

    vpa = _VPA.search(t)
    payee = _PAYEE_PARENS.search(t)
    if payee:
        f.counterparty = payee.group(1).strip()
    elif vpa:
        f.counterparty = vpa.group(1)

    nar = _NARRATION.search(t)
    if nar:
        val = nar.group(1)
        cut = _NARRATION_END.search(val)
        f.narration = (val[:cut.start()] if cut else val).strip(" .-_") or None

    ref = _REFERENCE.search(t) or _IMPS_REF.search(t)
    if ref:
        f.reference = ref.group(1)

    for name, rx in _HINTS:
        if rx.search(t):
            f.kind_hint = name
            break

    got = sum(1 for v in (f.amount, f.direction, f.occurred_on, f.account_mask) if v is not None)
    f.confidence = "high" if got >= 3 else "partial" if got >= 2 else "low"
    return f
