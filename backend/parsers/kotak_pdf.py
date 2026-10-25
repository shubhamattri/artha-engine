"""Kotak credit-card PDF statement parser.

Kotak statements are sometimes laid out as tables and sometimes as flowing
text. We try extract_tables() first and fall back to extract_text(). Dates are
DD-MM-YYYY, DD/MM/YYYY or DD-Mon-YYYY; direction is signalled by "(DR)" / "(CR)" markers.

Kotak redesigned the statement in 2026: the header became "Date Description Spends Category
Amount (₹)" and dates switched to "31-Mar-2026". A numeric-only month pattern silently dropped
every row, so a redesign reads as an empty statement rather than an error. Accept both spellings.
"""

from __future__ import annotations

import re

import pdfplumber

from parsers.base import ParsedTransaction, compute_stmt_month

_BANK = "Kotak"

_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}

_MONTH_PART = r"\d{1,2}|[A-Za-z]{3,9}"
_DATE = re.compile(rf"(\d{{1,2}})[/-]({_MONTH_PART})[/-](\d{{2,4}})")
_LEADING_DATE = re.compile(rf"^\s*(\d{{1,2}}[/-](?:{_MONTH_PART})[/-]\d{{2,4}})\s+(.*)$")
# Amount near end of line with optional (DR)/(CR) marker.
_AMOUNT = re.compile(
    r"([\d,]+\.\d{2}|\d[\d,]*)\s*(\(DR\)|\(CR\)|DR|CR)?\s*$",
    re.IGNORECASE,
)
# Letter boundaries, not \b: "CRN:475529591" and merchants like MICROSOFT contain CR, and without
# them a debit silently reads as a credit, which flips the sign on real money.
_DC_MARKER = re.compile(r"\(?\s*(?<![A-Za-z])(DR|CR)(?![A-Za-z])\s*\)?", re.IGNORECASE)


def _norm_date(token: str) -> str | None:
    m = _DATE.search(token)
    if not m:
        return None
    raw_mo = m.group(2)
    if raw_mo.isdigit():
        mo = int(raw_mo)
    else:
        mo = _MONTHS.get(raw_mo[:3].lower(), 0)
    d, y = int(m.group(1)), m.group(3)
    y = int("20" + y) if len(y) == 2 else int(y)
    if 1 <= d <= 31 and 1 <= mo <= 12:
        return f"{y:04d}-{mo:02d}-{d:02d}"
    return None


def _to_amount(raw: str) -> float | None:
    s = str(raw).replace(",", "").strip()
    for junk in ("rs.", "rs", "inr", "₹"):
        s = s.replace(junk, "")
    s = s.strip()
    try:
        return abs(float(s))
    except ValueError:
        return None


def _direction_from(text: str) -> str:
    m = _DC_MARKER.search(text)
    if m and m.group(1).lower() == "cr":
        return "credit"
    return "debit"


def _row_from_cells(
    cells: list[str], card_type: str, card_last4: str
) -> ParsedTransaction | None:
    """Build a transaction from a table row's cells, if it looks valid."""
    cells = [str(c).strip() for c in cells if c is not None and str(c).strip()]
    if len(cells) < 2:
        return None

    date = _norm_date(cells[0])
    if date is None:
        return None

    # Find the rightmost cell that parses as an amount.
    amount = None
    amount_idx = None
    for i in range(len(cells) - 1, 0, -1):
        cand = _to_amount(cells[i])
        if cand is not None and cand > 0:
            amount = cand
            amount_idx = i
            break
    if amount is None:
        return None

    joined = " ".join(cells)
    direction = _direction_from(joined)

    desc_cells = cells[1:amount_idx] if amount_idx is not None else cells[1:]
    description = " ".join(desc_cells)
    description = _DC_MARKER.sub("", description)
    description = re.sub(r"\s{2,}", " ", description).strip(" -|")
    if not description:
        return None

    return ParsedTransaction(
        bank=_BANK,
        card_type=card_type,
        card_last4=card_last4,
        stmt_month=compute_stmt_month(date),
        date=date,
        description=description,
        amount=amount,
        direction=direction,
    )


def _parse_tables(filepath: str, card_type: str, card_last4: str, password: str = ""):
    results: list[ParsedTransaction] = []
    with pdfplumber.open(filepath, password=password) as pdf:
        for page in pdf.pages:
            try:
                tables = page.extract_tables() or []
            except Exception:
                tables = []
            for table in tables:
                for row in table or []:
                    try:
                        txn = _row_from_cells(row, card_type, card_last4)
                        if txn is not None:
                            results.append(txn)
                    except Exception:
                        continue
    return results


def _parse_text(filepath: str, card_type: str, card_last4: str, password: str = ""):
    results: list[ParsedTransaction] = []
    chunks: list[str] = []
    with pdfplumber.open(filepath, password=password) as pdf:
        for page in pdf.pages:
            try:
                txt = page.extract_text() or ""
            except Exception:
                txt = ""
            if txt:
                chunks.append(txt)
    text = "\n".join(chunks)

    for line in text.splitlines():
        try:
            lead = _LEADING_DATE.match(line)
            if not lead:
                continue
            date = _norm_date(lead.group(1))
            if date is None:
                continue
            rest = lead.group(2).strip()
            amt_match = _AMOUNT.search(rest)
            if not amt_match:
                continue
            amount = _to_amount(amt_match.group(1))
            if amount is None or amount == 0:
                continue
            direction = _direction_from(rest)
            description = rest[: amt_match.start()].strip()
            description = _DC_MARKER.sub("", description)
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


def parse_kotak_pdf(
    filepath: str, card_type: str, card_last4: str, password: str = ""
) -> list[ParsedTransaction]:
    """Parse a Kotak PDF: tables first, text fallback."""
    try:
        results = _parse_tables(filepath, card_type, card_last4, password)
    except Exception:
        results = []

    if results:
        return results

    try:
        return _parse_text(filepath, card_type, card_last4, password)
    except Exception:
        return []
