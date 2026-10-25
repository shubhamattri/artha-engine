"""Kotak bank-account statement parser (CSV export).

Kotak's account-statement CSV has a header block (customer/account metadata),
then a transaction table whose header row looks like:

    Sl. No. | Transaction Date | Value Date | Description | Chq /Ref No. | Amount | Dr / Cr | Balance | Dr / Cr

Example data row:
    1 | 01-12-2025 20:27:15 | 01-12-2025 | UPI/Example Bakers/... | UPI-400111222333 | 890.00 | DR | 15,000.00 | CR

Kotak CSV layout: amount lives in the Amount column
with a Dr/Cr flag in the next column -> debit if flag==DR else credit. We read
the amount and direction from those EXPLICIT columns, never from "the largest
number on the row" (that would pick up the running Balance).

The footer carries the closing balance:
    Closing Balance | as on 20/06/2026 INR 1,000.00
Opening balance is back-computed from the first transaction's balance.
"""

from __future__ import annotations

import csv
import re

from parsers.base import ParsedTransaction, compute_stmt_month

_BANK = "Kotak"


def _norm_date(value) -> str | None:
    """Convert Kotak's 'DD-MM-YYYY HH:MM:SS' (or 'DD-MM-YYYY') to yyyy-mm-dd."""
    if value is None:
        return None
    s = str(value).strip()
    if s in ("", "nan", "NaT"):
        return None
    # Drop any time component.
    s = s.split(" ")[0].strip()
    parts = s.replace("/", "-").split("-")
    if len(parts) != 3:
        return None
    try:
        dd, mm, yy = (p.strip() for p in parts)
        y = int(yy)
        if y < 100:
            y += 2000
        d, m = int(dd), int(mm)
        if not (1 <= d <= 31 and 1 <= m <= 12):
            return None
        return f"{y:04d}-{m:02d}-{d:02d}"
    except ValueError:
        return None


def _to_amount(value) -> float | None:
    if value is None:
        return None
    s = str(value).replace(",", "").replace("₹", "").replace("INR", "").strip()
    if s in ("", "nan", "-"):
        return None
    try:
        v = abs(float(s))
        return v if v > 0 else None
    except ValueError:
        return None


def _read_rows(filepath: str) -> list[list[str]]:
    """Read the CSV into a list of cell-lists, tolerating bad bytes."""
    with open(filepath, newline="", encoding="utf-8", errors="replace") as f:
        return list(csv.reader(f))


def _find_header(rows: list[list[str]]) -> int | None:
    """Locate the transaction header row by its column labels."""
    for r, cells in enumerate(rows[:60]):
        joined = " ".join(str(c).strip().lower() for c in cells)
        if (
            "transaction date" in joined
            and "description" in joined
            and "amount" in joined
            and "balance" in joined
        ):
            return r
    return None


def parse_kotak_bank(
    filepath: str, card_type: str, card_last4: str
) -> list[ParsedTransaction]:
    """Parse a Kotak account-statement CSV into ParsedTransaction rows."""
    try:
        rows = _read_rows(filepath)
    except Exception:
        return []

    header_row = _find_header(rows)
    if header_row is None:
        return []

    results: list[ParsedTransaction] = []
    for r in range(header_row + 1, len(rows)):
        try:
            row = rows[r]
            # Transaction rows have the full set of columns; footer/notes are short.
            if len(row) < 8:
                continue

            date = _norm_date(row[1])
            if date is None:
                continue  # footer, blank, or "Closing Balance" rows

            description = str(row[3] or "").strip()
            if not description or description.lower() == "nan":
                continue

            amount = _to_amount(row[5])
            if amount is None:
                continue

            flag = str(row[6] or "").strip().upper()
            if flag == "DR":
                direction = "debit"
            elif flag == "CR":
                direction = "credit"
            else:
                continue  # unknown flag -> skip rather than guess

            results.append(
                ParsedTransaction(
                    bank=_BANK,
                    card_type=card_type or "Account",
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


def get_open_close(filepath: str) -> tuple[float | None, float | None]:
    """Return (opening, closing) balance for the balance-gate.

    Closing comes from the footer line 'Closing Balance ... INR <amount>'.
    Opening is back-computed from the first transaction's printed balance:
        opening = first.balance - first.credit + first.debit
    Returns (None, None) if either cannot be determined.
    """
    try:
        rows = _read_rows(filepath)
    except Exception:
        return (None, None)

    header_row = _find_header(rows)
    if header_row is None:
        return (None, None)

    # --- opening: back-compute from the first transaction row ---
    opening: float | None = None
    for r in range(header_row + 1, len(rows)):
        row = rows[r]
        if len(row) < 8:
            continue
        if _norm_date(row[1]) is None:
            continue
        balance = _to_amount(row[7])
        amount = _to_amount(row[5])
        flag = str(row[6] or "").strip().upper()
        if balance is None or amount is None or flag not in ("DR", "CR"):
            continue
        # Balance is post-transaction. Reverse it to get the pre-first balance.
        if flag == "DR":
            opening = balance + amount  # money left, so before it was higher
        else:
            opening = balance - amount  # money came in, before it was lower
        break

    # --- closing: scan footer for the 'Closing Balance' line ---
    closing: float | None = None
    for row in rows[header_row + 1 :]:
        joined = " ".join(str(c) for c in row)
        if "closing balance" in joined.lower():
            m = re.search(r"INR\s*([\d,]+\.\d{2})", joined, re.IGNORECASE)
            if not m:
                m = re.search(r"([\d,]+\.\d{2})", joined)
            if m:
                closing = _to_amount(m.group(1))
            break

    # Fallback: if no footer line, use the last transaction's printed balance.
    if closing is None:
        for r in range(len(rows) - 1, header_row, -1):
            row = rows[r]
            if len(row) < 8 or _norm_date(row[1]) is None:
                continue
            closing = _to_amount(row[7])
            if closing is not None:
                break

    return (opening, closing)
