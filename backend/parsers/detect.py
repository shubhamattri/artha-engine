"""Auto-detect which bank/card a statement is, by sniffing filename + content.

So the user can just drop a statement and Artha figures out the bank, card type, and
last-4 — no manual "which bank?" picker. Returns best-effort fields; the upload route
falls back to a friendly "please tell us the bank" only when detection can't decide.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Bank keyword → our parser key. First match wins (order matters for overlaps).
_BANK_KEYWORDS = [
    ("hdfc", "hdfc"),
    ("icici", "icici"),
    ("kotak", "kotak"),
    ("axis", "axis"),
    ("state bank", "sbi"), ("sbi", "sbi"),
    ("american express", "amex"), ("amex", "amex"),
]

_CREDIT_MARKERS = ("credit card", "card statement", "statement of credit", "card no",
                   "total amount due", "minimum amount due", "available credit limit")
_ACCOUNT_MARKERS = ("account statement", "statement of account", "savings account",
                    "account number", "closing balance", "available balance")

# last-4 from a masked card/account number, or "ending 1234" / "XXXX1234"
_LAST4_PATTERNS = [
    re.compile(r"(?:ending|ending in|card no\.?|a/c no\.?|account no\.?)\s*[:#]?\s*[xX*\d ]*?(\d{4})\b"),
    re.compile(r"[xX*]{4,}\s*(\d{4})\b"),
    re.compile(r"\d{4}[xX*\s]{4,}(\d{4})\b"),
]


@dataclass
class Detection:
    bank: str | None
    card_type: str          # "Credit Card" | "Account"
    card_last4: str | None
    confidence: str          # high | medium | low
    text_sniffed: bool


def _sniff_text(path: str, ext: str, password: str = "") -> str:
    """Pull a chunk of readable text from the first page/rows for keyword matching."""
    try:
        if ext == ".pdf":
            import pdfplumber  # noqa: PLC0415
            with pdfplumber.open(path, password=password) as pdf:
                pages = pdf.pages[:2]
                return "\n".join((p.extract_text() or "") for p in pages)
        if ext == ".csv":
            with open(path, encoding="utf-8", errors="ignore") as f:
                return f.read(8000)
        if ext in (".xlsx", ".xls"):
            try:
                import openpyxl  # noqa: PLC0415
                wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
                ws = wb.active
                cells = []
                for i, row in enumerate(ws.iter_rows(values_only=True)):
                    if i > 25:
                        break
                    cells += [str(c) for c in row if c is not None]
                return " ".join(cells)
            except Exception:
                return ""
    except Exception:
        return ""
    return ""


def detect_statement(path: str, ext: str, filename: str, password: str = "") -> Detection:
    text = _sniff_text(path, ext, password)
    hay = f"{filename} {text}".lower()

    # Bank = the ISSUER, read from the filename + statement header only. The transaction
    # body is full of other banks' UPI handles (@hdfcbank, @icici, @ybl…) which would
    # otherwise mis-route an Axis statement to HDFC, etc.
    header = f"{filename} {text[:400]}".lower()
    bank = next((key for kw, key in _BANK_KEYWORDS if kw in header), None)

    # A redesign that leads with a long name/address block pushes the issuer past 400 chars, and the
    # statement is then refused for an unknown bank rather than parsed. Widening the window for
    # everyone would reintroduce the mis-routing above, so only fall back when the header found
    # nothing, and only accept an outright winner by frequency — the issuer names itself repeatedly,
    # an incidental UPI handle appears once or twice.
    if bank is None and text:
        counts: dict[str, int] = {}
        low = text.lower()
        for kw, key in _BANK_KEYWORDS:
            if (n := low.count(kw)):
                counts[key] = counts.get(key, 0) + n
        if counts:
            ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
            if len(ranked) == 1 or ranked[0][1] > ranked[1][1]:
                bank = ranked[0][0]

    # Excel exports are bank-account statements; PDFs/CSVs are usually credit cards.
    is_account = any(m in hay for m in _ACCOUNT_MARKERS) or ext in (".xls", ".xlsx")
    is_credit = any(m in hay for m in _CREDIT_MARKERS)
    card_type = "Account" if (is_account and not is_credit) else "Credit Card"

    last4 = None
    for pat in _LAST4_PATTERNS:
        m = pat.search(text)
        if m:
            last4 = m.group(1)
            break

    if bank and (is_account or is_credit) and last4:
        conf = "high"
    elif bank:
        conf = "medium"
    else:
        conf = "low"
    return Detection(bank=bank, card_type=card_type, card_last4=last4,
                     confidence=conf, text_sniffed=bool(text))
