"""Unit tests for pure backend logic — no DB or real statements required."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parsers.base import ParsedTransaction, compute_fingerprint, compute_stmt_month
from parsers.categorizer import categorize
from services.ingest import select_parser as _select_parser


def _txn(desc="SWIGGY", amount=100.0, direction="debit"):
    return ParsedTransaction(
        bank="HDFC", card_type="Platinum", card_last4="9922",
        stmt_month="2026-05", date="2026-05-10",
        description=desc, amount=amount, direction=direction,
    )


# ---- fingerprint ----
def test_fingerprint_is_stable():
    assert compute_fingerprint(_txn()) == compute_fingerprint(_txn())


def test_fingerprint_seq_distinguishes_identical_rows():
    a = compute_fingerprint(_txn(), seq=0)
    b = compute_fingerprint(_txn(), seq=1)
    assert a != b  # two identical charges in one statement stay distinct


def test_fingerprint_changes_with_amount():
    assert compute_fingerprint(_txn(amount=100)) != compute_fingerprint(_txn(amount=200))


def test_stmt_month():
    assert compute_stmt_month("2026-05-10") == "2026-05"


# ---- categorizer ----
def test_categorize_known_merchants():
    assert categorize("SWIGGY order")[0] == "Food & Dining"
    assert categorize("AMAZON purchase")[0] == "Shopping"
    cat, conf = categorize("zzz unknown merchant")
    assert cat == "Personal / Misc" and conf == "low"  # default bucket renamed Others → Personal / Misc


# ---- upload routing / rejection contract ----
def test_select_parser_valid_combos():
    for bank, ext in [("hdfc", ".csv"), ("icici", ".pdf"), ("kotak", ".pdf"), ("axis", ".pdf")]:
        parser, err = _select_parser(bank, ext)
        assert parser is not None and err is None


def test_select_parser_excel_any_bank():
    parser, err = _select_parser("hdfc", ".xls")
    assert parser is not None and err is None


def test_select_parser_wrong_type_is_polite():
    parser, err = _select_parser("kotak", ".csv")
    assert parser is None and "pdf" in err.lower()


def test_hdfc_pdf_statements_are_readable():
    """HDFC emails PDFs and only exports CSV on request. Rejecting the PDF left nine statements
    unimportable, so the spend they carried was missing rather than merely awkward to load."""
    parser, err = _select_parser("hdfc", ".pdf")
    assert parser is not None and err is None


def test_select_parser_unknown_bank_is_polite():
    """Polite is not enough — the refusal must also say what DOES work, so an unreadable PDF is not
    mistaken for "this card cannot be tracked". SMS capture and Excel import both work for any bank."""
    parser, err = _select_parser("yesbank", ".pdf")
    assert parser is None
    low = err.lower()
    assert "can't read" in low
    assert "excel" in low or "csv" in low
    assert "accounts" in low


def test_select_parser_unreadable_type_is_polite():
    parser, err = _select_parser("hdfc", ".txt")
    assert parser is None and "can't read" in err.lower()
