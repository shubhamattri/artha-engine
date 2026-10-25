"""ICICI bank-account statement parser (.xls export).

ICICI's "DETAILED STATEMENT" .xls has a search/filter block at the top, then a
transaction table whose header row looks like:

    S No. | Value Date | Transaction Date | Cheque Number | Transaction Remarks | Withdrawal Amount(INR) | Deposit Amount(INR) | Balance(INR)

(there is a leading all-NaN spacer column at index 0, so the real columns are
shifted right by one).

ICICI .xls layout: debit=Withdrawal(col6),
credit=Deposit(col7), balance=Balance(col8); accept a row ONLY if the Serial-No
column is an integer (this skips the search/filter rows above the table and the
legend rows below it). Dates are emitted as 'DD,MM,YYYY' (comma-separated).

Withdrawal => debit, Deposit => credit. Amounts are read from the EXPLICIT
withdrawal/deposit columns, never inferred from the row's largest number.
"""

from __future__ import annotations

import pandas as pd

from parsers.base import ParsedTransaction, compute_stmt_month

_BANK = "ICICI"

# Column indices (0-indexed, incl. leading spacer).
_COL_SNO = 1
_COL_VALUE_DATE = 2
_COL_TXN_DATE = 3
_COL_REMARKS = 5
_COL_WITHDRAWAL = 6
_COL_DEPOSIT = 7
_COL_BALANCE = 8


def _norm_date(value) -> str | None:
    """Convert ICICI's 'DD,MM,YYYY' (or a pandas Timestamp) to yyyy-mm-dd."""
    if value is None:
        return None
    s = str(value).strip()
    if s in ("", "nan", "NaT"):
        return None
    # pandas may hand us a Timestamp string "2025-12-05 00:00:00".
    if "-" in s and s[:4].isdigit():
        return s[:10]
    parts = s.replace("/", ",").replace("-", ",").replace(".", ",").split(",")
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


def _is_serial(value) -> bool:
    """True only if the cell is an integer serial number (the row-accept gate)."""
    if value is None:
        return False
    s = str(value).strip()
    if s in ("", "nan"):
        return False
    # pandas often reads integer cells as floats ("1.0"); accept those too.
    try:
        f = float(s)
        return f == int(f) and f >= 1
    except ValueError:
        return False


def _read_frame(filepath: str) -> pd.DataFrame:
    """Read the sheet with no header, trying engines for both .xls and .xlsx."""
    last_err: Exception | None = None
    for engine in ("xlrd", "openpyxl", None):
        try:
            return pd.read_excel(filepath, header=None, engine=engine)
        except Exception as e:  # noqa: BLE001 - try the next engine
            last_err = e
    raise last_err if last_err else RuntimeError("could not read Excel file")


def parse_icici_bank(
    filepath: str, card_type: str, card_last4: str
) -> list[ParsedTransaction]:
    """Parse an ICICI account-statement .xls into ParsedTransaction rows."""
    try:
        df = _read_frame(filepath)
    except Exception:
        return []

    results: list[ParsedTransaction] = []
    for r in range(len(df)):
        try:
            row = df.iloc[r].tolist()

            def cell(idx: int):
                return row[idx] if idx < len(row) else None

            # Row-accept gate: Serial-No column must be an integer.
            if not _is_serial(cell(_COL_SNO)):
                continue

            # Prefer the Transaction Date; fall back to Value Date.
            date = _norm_date(cell(_COL_TXN_DATE)) or _norm_date(cell(_COL_VALUE_DATE))
            if date is None:
                continue

            remarks = str(cell(_COL_REMARKS) or "").strip()
            if not remarks or remarks.lower() == "nan":
                continue

            withdrawal = _to_amount(cell(_COL_WITHDRAWAL))
            deposit = _to_amount(cell(_COL_DEPOSIT))
            if withdrawal is not None:
                amount, direction = withdrawal, "debit"
            elif deposit is not None:
                amount, direction = deposit, "credit"
            else:
                continue  # both zero/blank -> not a money-moving row

            results.append(
                ParsedTransaction(
                    bank=_BANK,
                    card_type=card_type or "Account",
                    card_last4=card_last4,
                    stmt_month=compute_stmt_month(date),
                    date=date,
                    description=remarks,
                    amount=amount,
                    direction=direction,
                )
            )
        except Exception:
            continue

    return results


def get_open_close(filepath: str) -> tuple[float | None, float | None]:
    """Return (opening, closing) balance for the balance-gate.

    There is no explicit opening/closing line, so:
      - opening is back-computed from the first transaction row:
            opening = first.balance - first.deposit + first.withdrawal
      - closing is the last transaction row's printed Balance.
    Returns (None, None) if no transaction rows are found.
    """
    try:
        df = _read_frame(filepath)
    except Exception:
        return (None, None)

    txn_rows: list[list] = []
    for r in range(len(df)):
        row = df.iloc[r].tolist()
        sno = row[_COL_SNO] if _COL_SNO < len(row) else None
        if _is_serial(sno):
            txn_rows.append(row)

    if not txn_rows:
        return (None, None)

    def _bal(row) -> float | None:
        return _to_amount(row[_COL_BALANCE]) if _COL_BALANCE < len(row) else None

    def _wd(row) -> float:
        v = _to_amount(row[_COL_WITHDRAWAL]) if _COL_WITHDRAWAL < len(row) else None
        return v or 0.0

    def _dep(row) -> float:
        v = _to_amount(row[_COL_DEPOSIT]) if _COL_DEPOSIT < len(row) else None
        return v or 0.0

    first = txn_rows[0]
    first_bal = _bal(first)
    opening: float | None = None
    if first_bal is not None:
        # Balance is post-transaction. Reverse it to get the pre-first balance.
        opening = first_bal - _dep(first) + _wd(first)

    # closing = last row that actually has a printed balance.
    closing: float | None = None
    for row in reversed(txn_rows):
        b = _bal(row)
        if b is not None:
            closing = b
            break

    return (opening, closing)
