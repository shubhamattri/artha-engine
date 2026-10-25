"""ICICI credit-card PDF statement parser.

ICICI statements are text-based PDFs. Transaction rows follow:
    DATE  DESCRIPTION  AMOUNT  [Dr|Cr]
with the date as DD/MM/YYYY or DD MMM YYYY, and a Dr/Cr suffix indicating
direction. ICICI typically marks spends as "Dr" and payments/refunds as "Cr".
"""

from __future__ import annotations

import re

import pdfplumber

from parsers.base import ParsedTransaction, compute_stmt_month

_BANK = "ICICI"

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# DD/MM/YYYY or DD-MM-YYYY
_DATE_NUMERIC = re.compile(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})$")
# DD MMM YYYY  (e.g. "05 Jan 2026")
_DATE_TEXT = re.compile(r"^(\d{1,2})\s+([A-Za-z]{3,})\s+(\d{2,4})$")

# Leading date forms used to anchor a transaction line.
_LEADING_DATE = re.compile(
    r"^\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{1,2}\s+[A-Za-z]{3,}\s+\d{2,4})\s+(.*)$"
)

# An amount possibly followed by a Dr/Cr marker, captured at end of line.
_AMOUNT_DC = re.compile(
    r"([\d,]+\.\d{2}|\d[\d,]*)\s*(Dr|Cr|DR|CR|dr|cr)?\s*$"
)


def _norm_date(token: str) -> str | None:
    token = token.strip()
    m = _DATE_NUMERIC.match(token)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
        y = int("20" + y) if len(y) == 2 else int(y)
        if 1 <= d <= 31 and 1 <= mo <= 12:
            return f"{y:04d}-{mo:02d}-{d:02d}"
        return None
    m = _DATE_TEXT.match(token)
    if m:
        d = int(m.group(1))
        mo = _MONTHS.get(m.group(2)[:3].lower())
        y = m.group(3)
        y = int("20" + y) if len(y) == 2 else int(y)
        if mo and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    return None


def _to_amount(raw: str) -> float | None:
    s = raw.replace(",", "").strip()
    for junk in ("rs.", "rs", "inr", "₹"):
        s = s.replace(junk, "")
    s = s.strip()
    try:
        return abs(float(s))
    except ValueError:
        return None


def _extract_text(filepath: str, password: str = "") -> str:
    chunks: list[str] = []
    with pdfplumber.open(filepath, password=password) as pdf:
        for page in pdf.pages:
            try:
                txt = page.extract_text() or ""
            except Exception:
                txt = ""
            if txt:
                chunks.append(txt)
    return "\n".join(chunks)


def parse_icici_pdf(
    filepath: str, card_type: str, card_last4: str, password: str = ""
) -> list[ParsedTransaction]:
    """Parse an ICICI credit-card PDF into ParsedTransaction rows."""
    try:
        text = _extract_text(filepath, password)
    except Exception:
        return []

    results: list[ParsedTransaction] = []

    for line in text.splitlines():
        try:
            lead = _LEADING_DATE.match(line)
            if not lead:
                continue

            date = _norm_date(lead.group(1))
            if date is None:
                continue

            rest = lead.group(2).strip()
            amt_match = _AMOUNT_DC.search(rest)
            if not amt_match:
                continue

            amount = _to_amount(amt_match.group(1))
            if amount is None or amount == 0:
                continue

            marker = (amt_match.group(2) or "").lower()
            direction = "credit" if marker == "cr" else "debit"

            description = rest[: amt_match.start()].strip()
            # Trim any trailing reference/amount artifacts already consumed.
            description = re.sub(r"\s{2,}", " ", description).strip(" -|")
            if not description:
                continue

            results.append(
                ParsedTransaction(
                    bank=_BANK,
                    card_type=card_type,
                    card_last4=card_last4,
                    stmt_month=compute_stmt_month(date),
                    date=date,
                    description=description,
                    amount=amount,
                    direction=direction,
                )
            )
        except Exception:
            continue

    return results
