"""HDFC credit-card CSV statement parser.

HDFC exports use a literal '~|~' separator. The file is a key-value header block
followed by a transactions section whose rows have the columns:

    Transaction type | Customer Name | DATE | Description | AMT | Debit/Credit | REWARDS

Example row:
    Domestic~|~CARDHOLDER NAME ~|~21/04/2026 11:09:09~|~SPOTIFY SI MUMBAI ~|~299.00~|~~|~

DATE carries a time suffix. The Debit/Credit column is empty for purchases and
"Cr" for payments/refunds. Amounts carry thousands-separator commas. Non-transaction
rows (header banner, account summary, section titles) are skipped defensively by
requiring the DATE column to parse as a real date.
"""

from __future__ import annotations

from parsers.base import ParsedTransaction, compute_stmt_month

_SEP = "~|~"
_BANK = "HDFC"


def _to_float(value) -> float | None:
    """Parse an amount cell into a positive float, or None if empty/invalid."""
    if value is None:
        return None
    s = str(value).strip()
    if s == "" or s.lower() in {"nan", "none"}:
        return None
    # Strip currency noise and thousands separators.
    for junk in ("rs.", "rs", "inr", "₹", ","):
        s = s.replace(junk, "")
    s = s.strip()
    if s == "":
        return None
    try:
        return abs(float(s))
    except ValueError:
        return None


def _parse_date(value) -> str | None:
    """Convert a DD/MM/YYYY (or DD-MM-YYYY) date cell to yyyy-mm-dd."""
    if value is None:
        return None
    s = str(value).strip()
    if s == "":
        return None
    sep = "/" if "/" in s else ("-" if "-" in s else None)
    if sep is None:
        return None
    parts = s.split(sep)
    if len(parts) != 3:
        return None
    try:
        dd, mm, yyyy = (p.strip() for p in parts)
        if len(yyyy) == 2:
            yyyy = "20" + yyyy
        d, m, y = int(dd), int(mm), int(yyyy)
        if not (1 <= d <= 31 and 1 <= m <= 12):
            return None
        return f"{y:04d}-{m:02d}-{d:02d}"
    except ValueError:
        return None


def _read_lines(filepath: str) -> list[str]:
    """Read the raw file as text lines, with an encoding fallback.

    HDFC's '~|~' is a *literal* multi-character delimiter. pandas.read_csv
    interprets a multi-char `sep` as a regex, where '|' means alternation, so
    it mis-splits these files. We split each line on the literal string
    instead, which is both correct and dependency-light.
    """
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(filepath, "r", encoding=encoding) as fh:
                return fh.read().splitlines()
        except (UnicodeDecodeError, UnicodeError):
            continue
        except Exception:
            break
    # Last resort: forgiving latin-1 decode.
    try:
        with open(filepath, "rb") as fh:
            return fh.read().decode("latin-1", errors="replace").splitlines()
    except Exception:
        return []


def parse_hdfc_csv(
    filepath: str, card_type: str, card_last4: str
) -> list[ParsedTransaction]:
    """Parse an HDFC CSV statement into ParsedTransaction rows."""
    lines = _read_lines(filepath)
    if not lines:
        return []

    # Transaction-row columns (0-indexed) within the transactions section.
    COL_DATE, COL_DESC, COL_AMT, COL_DRCR = 2, 3, 4, 5

    results: list[ParsedTransaction] = []

    for line in lines:
        try:
            if _SEP not in line:
                # Header banner / summary / blank lines without the delimiter.
                continue

            cells = [c.strip() for c in line.split(_SEP)]
            # A real transaction row has at least date, description and amount.
            if len(cells) <= COL_AMT:
                continue

            # The DATE cell carries a time suffix ("21/04/2026 11:09:09").
            date = _parse_date(cells[COL_DATE].split()[0] if cells[COL_DATE] else "")
            if date is None:
                # Header row ("DATE"), account-summary row, section title, etc.
                continue

            description = cells[COL_DESC].strip()
            if description == "":
                continue

            amount = _to_float(cells[COL_AMT])
            if amount is None or amount <= 0:
                continue

            # Empty Debit/Credit cell = purchase (debit); "Cr" = payment/refund.
            drcr = cells[COL_DRCR].strip().lower() if len(cells) > COL_DRCR else ""
            direction = "credit" if drcr.startswith("cr") else "debit"

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
            # Defensive: never let one malformed row kill the whole parse.
            continue

    return results
