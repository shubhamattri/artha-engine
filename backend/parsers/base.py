"""Base types and helpers shared across all statement parsers."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

# pdfminer (under pdfplumber) logs noisy "Could not get FontBBox" warnings on
# some bank PDFs. They're harmless — quiet them so real errors stay visible.
logging.getLogger("pdfminer").setLevel(logging.ERROR)


@dataclass
class ParsedTransaction:
    """A single transaction as extracted from a statement, before persistence."""

    bank: str
    card_type: str
    card_last4: str
    stmt_month: str  # yyyy-mm
    date: str  # yyyy-mm-dd
    description: str
    amount: float  # always positive
    direction: str  # "debit" or "credit"


def compute_fingerprint(t: ParsedTransaction, seq: int = 0) -> str:
    """Stable SHA1 fingerprint used to dedupe transactions across re-imports.

    Built from the fields that uniquely identify a transaction. Amount is
    normalised to 2 decimals so "100" and "100.00" collapse to one fingerprint.

    `seq` is the occurrence index among genuinely-identical rows within the SAME
    statement (a card can legitimately show two identical charges on one day).
    Including it keeps those distinct, while a re-import of the same statement
    reproduces the same fingerprints (so re-uploads stay idempotent).
    """
    raw = "|".join(
        [
            t.bank,
            t.card_last4,
            t.stmt_month,
            t.date,
            t.description.strip(),
            f"{t.amount:.2f}",
            t.direction,
            str(seq),
        ]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def compute_stmt_month(date_str: str) -> str:
    """Return yyyy-mm given a yyyy-mm-dd date string."""
    return date_str[:7]
