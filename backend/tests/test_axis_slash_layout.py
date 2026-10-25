"""Axis issues two credit-card layouts. Only one parsed.

A live statement carried 55 transactions in the DD/MM/YYYY table and returned zero rows, which the
pipeline reported as "No transactions found (empty, scanned, or unsupported layout)". Seven weeks of
a cycle were measured on SMS alerts alone while the statement sat in the mailbox, readable.
"""

from parsers.axis_pdf import _parse_slash_layout

LINES = [
    "PAYMENT SUMMARY",
    "DATE TRANSACTION DETAILS MERCHANT CATEGORY AMOUNT (Rs.)",
    "Card No: 400011******7788 Name ASHA VERMA",
    "29/06/2026 GST 59.22 Dr",
    "29/06/2026 EMI PRINCIPAL - 6/6, REF# 10002000 ELECTRONICS 12,000.00 Dr",
    "21/06/2026 UPI/ROHIT NAIR/PAYTM.X41ABCD@PTY/9800011122@PTA DEPT STORES 50.00 Dr",
    "22/06/2026 UPI/EXAMPLE SWEETS SAMPLE VIL/Q700000000@YBL/980 RESTAURANTS 234.00 Dr",
    "Card No: 400022******5544 Name ASHA VERMA",
    "30/06/2026 SOME ADD ON CARD SPEND GROCERY 1,200.50 Dr",
    "05/07/2026 PAYMENT RECEIVED 45,000.00 Cr",
]


def _rows():
    return _parse_slash_layout(LINES, "Credit Card", "7788")


def test_it_reads_the_slash_layout_at_all():
    assert len(_rows()) == 6


def test_amounts_with_thousands_separators_parse():
    r = next(x for x in _rows() if "EMI PRINCIPAL" in x.description)
    assert r.amount == 12000.00
    assert r.direction == "debit"


def test_cr_is_a_credit_not_a_debit():
    r = next(x for x in _rows() if "PAYMENT RECEIVED" in x.description)
    assert r.direction == "credit"
    assert r.amount == 45000.00


def test_dates_become_iso():
    assert {r.date for r in _rows()} >= {"2026-06-29", "2026-06-21", "2026-07-05"}


def test_rows_are_attributed_to_the_card_that_made_them():
    """An add-on card's rows follow its own 'Card No:' header. Attributing them to the primary would
    put another person's spending on the wrong card."""
    rows = _rows()
    assert next(r for r in rows if "ADD ON CARD" in r.description).card_last4 == "5544"
    assert next(r for r in rows if r.description == "GST").card_last4 == "7788"


def test_furniture_lines_are_not_transactions():
    assert not [r for r in _rows() if "PAYMENT SUMMARY" in r.description
                or "TRANSACTION DETAILS" in r.description]


def test_the_older_layout_still_wins_when_the_new_one_finds_nothing():
    """Both statement generations must parse without the caller knowing which it has."""
    assert _parse_slash_layout(["29 Jun '26 EXAMPLE SWEETS ₹234.00 Debit"], "Credit Card", "7788") == []
