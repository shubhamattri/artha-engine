"""HDFC credit-card PDF statement parser.

HDFC emails a PDF and only exports CSV on request, so a household that never asks for the export
had no way to import these at all — nine statements sat unreadable and the spend they carried was
simply absent. Rows look like:

    22/04/2026| 07:36 EXAMPLEGROCERYBENGALURU             C 1,000.00 l
    22/04/2026| 07:36 CREDIT CARD PAYMENT...          + C 50,000.00 l
    11/05/2026 | 17:42 EXAMPLECLOUD USD 12.34 + 28 C 1,050.00 l

Three things that are easy to get wrong, and cost real money when you do:

* The "C" before every amount is the rupee glyph; pdfplumber renders ₹ as C with these fonts.
* A bare "+" immediately before the amount marks a CREDIT. A "+" followed by a number is the
  REWARDS column (points earned) on a debit — cards with a rewards column have both spellings.
* International Transactions is a separate section whose rows put a space before the "|". Missing
  it under-reported one statement's debits by ₹3,500.00 while its credits still reconciled, so
  the totals looked plausible and only the debit side was wrong.

Descriptions wrap: a long merchant string is emitted on the lines around the amount line rather
than inside it, so an un-stitched row parses with an empty description.
"""

from __future__ import annotations

import re

import pdfplumber

from parsers.base import ParsedTransaction, compute_stmt_month

_BANK = "HDFC"

# DATE | TIME, description, optional rewards/credit marker, then the ₹-as-C amount.
_ROW = re.compile(
    r"^(\d{2})/(\d{2})/(\d{4})\s*\|\s*\d{2}:\d{2}\s+"
    r"(?P<desc>.*?)\s*(?P<credit>\+)?\s*C\s*(?P<amt>[\d,]+\.\d{2})\s*l?\s*$"
)
# Any line that is structurally part of the statement chrome rather than a wrapped description.
_NOISE = re.compile(
    r"(?i)^(page \d|.*GSTIN|.*HSN Code|DATE & TIME|Domestic Transactions|International Transactions"
    r"|Offers on your card|\*Transaction time|TRANSACTIONS TOTAL|Reward Points|.*Credit Card Statement)"
)
_TRAILING_REWARDS = re.compile(r"\s*\+\s*\d+\s*$")


def _to_amount(raw: str) -> float | None:
    try:
        return abs(float(raw.replace(",", "")))
    except (ValueError, AttributeError):
        return None


def _is_wrappable(line: str) -> bool:
    """A neighbouring line that could hold the rest of a wrapped description."""
    s = line.strip()
    return bool(s) and not _ROW.match(s) and not _NOISE.match(s)


def _clean(desc: str) -> str:
    # A rewards count that stayed glued to the description is a column, not part of the name.
    desc = _TRAILING_REWARDS.sub("", desc)
    return re.sub(r"\s{2,}", " ", desc).strip(" -|")


def _parse_lines(
    lines: list[str], card_type: str, card_last4: str
) -> list[ParsedTransaction]:
    out: list[ParsedTransaction] = []

    for i, line in enumerate(lines):
        m = _ROW.match(line.strip())
        if not m:
            continue
        amount = _to_amount(m.group("amt"))
        if amount is None or amount == 0:
            continue

        dd, mm, yyyy = m.group(1), m.group(2), m.group(3)
        day, month = int(dd), int(mm)
        if not (1 <= day <= 31 and 1 <= month <= 12):
            continue
        date = f"{yyyy}-{month:02d}-{day:02d}"

        description = _clean(m.group("desc"))
        if not description:
            # Stitch the wrapped halves: HDFC emits them immediately before and after the row.
            parts = []
            if i > 0 and _is_wrappable(lines[i - 1]):
                parts.append(lines[i - 1].strip())
            if i + 1 < len(lines) and _is_wrappable(lines[i + 1]):
                parts.append(lines[i + 1].strip())
            description = _clean(" ".join(parts))
        if not description:
            continue

        out.append(
            ParsedTransaction(
                bank=_BANK,
                card_type=card_type,
                card_last4=card_last4,
                stmt_month=compute_stmt_month(date),
                date=date,
                description=description,
                amount=amount,
                direction="credit" if m.group("credit") else "debit",
            )
        )
    return out


def parse_hdfc_pdf(
    filepath: str, card_type: str, card_last4: str, password: str = ""
) -> list[ParsedTransaction]:
    """Parse an HDFC credit-card PDF statement."""
    with pdfplumber.open(filepath, password=password) as pdf:
        text = "\n".join((page.extract_text() or "") for page in pdf.pages)
    return _parse_lines([ln.rstrip() for ln in text.split("\n")], card_type, card_last4)
