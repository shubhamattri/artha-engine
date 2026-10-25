"""Excel bank-account statement parser (HDFC-style .xls/.xlsx).

Bank *account* statements (as opposed to credit-card statements) are exported as
Excel with a header block, then a transaction table:

    Date | Narration | Chq./Ref.No. | Value Dt | Withdrawal Amt. | Deposit Amt. | Closing Balance
    01/12/25 | ACH C- EXAMPLE INDUSTRIES | ... | 01/12/25 | 227.50 | | 10,250.00

We locate the header row dynamically (by its column labels) and read the rows
below it. Withdrawal => debit, Deposit => credit.
"""

from __future__ import annotations

import pandas as pd

from parsers.base import ParsedTransaction, compute_stmt_month

_BANK_DEFAULT = "HDFC"


def _norm_date(value) -> str | None:
    """Convert a DD/MM/YY(YY) date (or a pandas Timestamp) to yyyy-mm-dd."""
    if value is None:
        return None
    s = str(value).strip()
    if s in ("", "nan", "NaT"):
        return None
    # pandas may hand us a Timestamp string "2025-12-01 00:00:00".
    if "-" in s and s[:4].isdigit():
        return s[:10]
    parts = s.replace("-", "/").split("/")
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
    s = str(value).replace(",", "").replace("₹", "").strip()
    if s in ("", "nan", "-"):
        return None
    try:
        v = abs(float(s))
        return v if v > 0 else None
    except ValueError:
        return None


def _read_frame(filepath: str) -> pd.DataFrame:
    """Read the sheet with no header, trying engines for both .xls and .xlsx."""
    last_err: Exception | None = None
    for engine in ("xlrd", "openpyxl", None):
        try:
            return pd.read_excel(filepath, header=None, engine=engine)
        except Exception as e:  # noqa: BLE001 - try the next engine
            last_err = e
    raise last_err if last_err else RuntimeError("could not read Excel file")


def _find_columns(df: pd.DataFrame) -> tuple[int, dict[str, int]] | None:
    """Locate the header row and map logical columns to indices."""
    for r in range(min(60, len(df))):
        cells = [str(x).strip().lower() for x in df.iloc[r].tolist()]
        joined = " ".join(cells)
        if "date" in joined and "narration" in joined and (
            "withdrawal" in joined or "deposit" in joined
        ):
            cols: dict[str, int] = {}
            for i, c in enumerate(cells):
                if c.startswith("date") and "date" not in cols:
                    cols["date"] = i
                elif "narration" in c:
                    cols["narration"] = i
                elif "withdrawal" in c:
                    cols["withdrawal"] = i
                elif "deposit" in c:
                    cols["deposit"] = i
            if {"date", "narration"} <= cols.keys() and (
                "withdrawal" in cols or "deposit" in cols
            ):
                return r, cols
    return None


def parse_account_excel(
    filepath: str, card_type: str, card_last4: str
) -> list[ParsedTransaction]:
    """Parse an Excel bank-account statement into ParsedTransaction rows."""
    df = _read_frame(filepath)
    found = _find_columns(df)
    if found is None:
        return []
    header_row, cols = found

    results: list[ParsedTransaction] = []
    for r in range(header_row + 1, len(df)):
        try:
            row = df.iloc[r].tolist()

            def cell(key: str):
                idx = cols.get(key)
                return row[idx] if idx is not None and idx < len(row) else None

            date = _norm_date(cell("date"))
            if date is None:
                continue  # separator rows, footer, totals

            narration = str(cell("narration") or "").strip()
            if not narration or narration.lower() == "nan":
                continue

            withdrawal = _to_amount(cell("withdrawal"))
            deposit = _to_amount(cell("deposit"))
            if withdrawal is not None:
                amount, direction = withdrawal, "debit"
            elif deposit is not None:
                amount, direction = deposit, "credit"
            else:
                continue

            results.append(
                ParsedTransaction(
                    bank=_BANK_DEFAULT,
                    card_type=card_type or "Account",
                    card_last4=card_last4,
                    stmt_month=compute_stmt_month(date),
                    date=date,
                    description=narration,
                    amount=amount,
                    direction=direction,
                )
            )
        except Exception:
            continue

    return results
