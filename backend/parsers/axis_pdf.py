"""Axis credit-card PDF statement parser.

Axis statements are text-based PDFs. Transaction rows look like:

    10 May '26 UPI/ExampleStore/store@axl/...  ₹ 2,400.00 Debit

i.e. date "DD MMM 'YY", a description, then "₹ AMOUNT" and a full-word
"Debit"/"Credit". Long descriptions WRAP across lines, leaving the date+amount
line bare with the description fragments on the lines immediately before/after:

    UPI/EXAMPLE TOYS P                  <- head fragment
    10 May '26 ₹ 1,200.00 Debit         <- bare amount line
    LTD/exampletoyspltd...@hdfcbank/    <- tail fragment

We anchor on the date+amount+direction line and stitch adjacent non-transaction
lines back into the description so categorisation still has the merchant text.
"""

from __future__ import annotations

import re

import pdfplumber

from parsers.base import ParsedTransaction, compute_stmt_month

_BANK = "Axis"

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# A full transaction line: date, optional inline description, ₹ amount, direction.
_TXN = re.compile(
    r"^(\d{1,2})\s+([A-Za-z]{3})\s+'?(\d{2,4})\s+"   # 1=day 2=mon 3=year
    r"(.*?)"                                          # 4=inline description (may be empty)
    r"₹\s*([\d,]+\.\d{2})\s+"                         # 5=amount
    r"(Debit|Credit)\s*$",                            # 6=direction
    re.IGNORECASE,
)
# Any date-led line (used to tell description fragments from transaction rows).
_DATE_LED = re.compile(r"^\d{1,2}\s+[A-Za-z]{3}\s+'?\d{2,4}\b")

# Axis issues a SECOND credit-card layout. Same bank, same detection, different table:
#     29/06/2026 UPI/EXAMPLE SWEETS/Q100200300@YBL/981 RESTAURANTS 234.00 Dr
# against the older
#     29 Jun '26 EXAMPLE SWEETS ₹234.00 Debit
# One statement carried 55 rows in this shape and parsed to zero, which read as "no transactions
# found" and left seven weeks of a live cycle measured on SMS alerts alone.
_TXN_SLASH = re.compile(
    r"^(\d{2})/(\d{2})/(\d{4})\s+"          # 1=dd 2=mm 3=yyyy
    r"(.+?)\s+"                               # 4=description (+ the bank's merchant category)
    r"([\d,]+\.\d{2})\s*"                    # 5=amount
    r"(Dr|Cr)\s*$",                           # 6=direction
    re.IGNORECASE,
)
# "Card No: 555000******3456" — an add-on card's rows follow until the next such line, so each row
# is attributed to the card that actually made it rather than to the primary.
_CARD_SECTION = re.compile(r"Card\s*No\.?\s*:?\s*\d{6}\*+(\d{4})", re.IGNORECASE)


def _parse_slash_layout(lines: list[str], card_type: str, card_last4: str) -> list[ParsedTransaction]:
    """Rows in Axis's DD/MM/YYYY layout.

    The bank's own merchant category is left on the end of the description rather than split out:
    the app classifies from the description with its own taxonomy, and guessing where the category
    starts would mangle payee names that are themselves uppercase.
    """
    out: list[ParsedTransaction] = []
    current = card_last4
    for line in lines:
        line = line.strip()
        sec = _CARD_SECTION.search(line)
        if sec:
            current = sec.group(1)
            continue
        m = _TXN_SLASH.match(line)
        if not m:
            continue
        dd, mm, yyyy, desc, amt, side = m.groups()
        try:
            date = f"{int(yyyy):04d}-{int(mm):02d}-{int(dd):02d}"
        except ValueError:
            continue
        amount = _to_amount(amt)
        if amount is None or amount <= 0:
            continue
        desc = re.sub(r"\s{2,}", " ", desc).strip(" -|")
        out.append(ParsedTransaction(
            bank=_BANK, card_type=card_type, card_last4=current or card_last4,
            stmt_month=compute_stmt_month(date), date=date,
            description=desc or "(no description)", amount=amount,
            direction="credit" if side.lower() == "cr" else "debit",
        ))
    return out


def _norm_date(day: str, mon: str, year: str) -> str | None:
    m = _MONTHS.get(mon.lower())
    if m is None:
        return None
    d = int(day)
    y = int(year)
    if y < 100:
        y += 2000
    if not (1 <= d <= 31):
        return None
    return f"{y:04d}-{m:02d}-{d:02d}"


def _to_amount(raw: str) -> float | None:
    s = raw.replace(",", "").replace("₹", "").strip()
    try:
        return abs(float(s))
    except ValueError:
        return None


def _is_fragment(line: str) -> bool:
    """A description-continuation line: not a transaction row, no amount marker."""
    s = line.strip()
    if not s:
        return False
    if _DATE_LED.match(s) or "₹" in s:
        return False
    # Skip obvious page furniture.
    low = s.lower()
    if low.startswith(("page ", "view ", "transaction", "date ")):
        return False
    return True


def parse_axis_pdf(
    filepath: str, card_type: str, card_last4: str, password: str = ""
) -> list[ParsedTransaction]:
    """Parse an Axis credit-card PDF into ParsedTransaction rows."""
    try:
        lines: list[str] = []
        with pdfplumber.open(filepath, password=password) as pdf:
            for page in pdf.pages:
                try:
                    txt = page.extract_text() or ""
                except Exception:
                    txt = ""
                lines.extend(txt.splitlines())
    except Exception:
        return []

    # Try the newer DD/MM/YYYY table first; fall through to the older layout when it finds nothing,
    # so both statement generations parse without the caller having to know which it has.
    slash = _parse_slash_layout(lines, card_type, card_last4)
    if slash:
        return slash

    results: list[ParsedTransaction] = []

    for i, line in enumerate(lines):
        try:
            m = _TXN.match(line.strip())
            if not m:
                continue

            date = _norm_date(m.group(1), m.group(2), m.group(3))
            if date is None:
                continue

            amount = _to_amount(m.group(5))
            if amount is None or amount <= 0:
                continue

            direction = "credit" if m.group(6).lower() == "credit" else "debit"

            description = m.group(4).strip()
            if not description:
                # Wrapped row — stitch head (prev) + tail (next) fragments.
                head = lines[i - 1].strip() if i > 0 and _is_fragment(lines[i - 1]) else ""
                tail = (
                    lines[i + 1].strip()
                    if i + 1 < len(lines) and _is_fragment(lines[i + 1])
                    else ""
                )
                description = f"{head} {tail}".strip()
            description = re.sub(r"\s{2,}", " ", description).strip(" -|")
            if not description:
                description = "(no description)"

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
