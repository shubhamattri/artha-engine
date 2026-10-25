"""Kotak redesigned its statement in 2026 and the parser silently returned nothing.

The header became "Date Description Spends Category Amount" and dates switched from 07/02/2026 to
31-Mar-2026. The date pattern required a numeric month, so every row was rejected and two
statements read as empty rather than as errors — including a 1,50,000.00 purchase and its payment.
"""

from parsers.kotak_pdf import _direction_from, _norm_date

NEW_LAYOUT = "31-Mar-2026 EXAMPLE MOTORS BENGALURU IN (Convert To EMI) Automotive 1,50,000.00"
OLD_LAYOUT = "07/02/2026 PAYMENTRECEIVED-MOBILEFUNDSTRANSFER 3,000.00 Cr"


def test_month_names_parse():
    assert _norm_date(NEW_LAYOUT) == "2026-03-31"


def test_numeric_months_still_parse():
    assert _norm_date(OLD_LAYOUT) == "2026-02-07"


def test_two_digit_years_still_parse():
    assert _norm_date("05-Jan-26 SOMETHING 10.00") == "2026-01-05"


def test_an_unknown_month_name_is_rejected_not_guessed():
    assert _norm_date("31-Xyz-2026 SOMETHING 10.00") is None


def test_trailing_cr_is_a_credit():
    assert _direction_from(OLD_LAYOUT) == "credit"


def test_a_purchase_with_no_marker_is_a_debit():
    assert _direction_from(NEW_LAYOUT) == "debit"


def test_cr_inside_a_word_does_not_flip_a_debit_to_a_credit():
    """CRN: appears in Kotak's page footer and merchants like MICROSOFT contain CR. Without letter
    boundaries the marker matches inside the word and money coming out reads as money coming in."""
    assert _direction_from("CRN:100200300 550 Page1of3 1,000.00") == "debit"
    assert _direction_from("31-Mar-2026 MICROSOFT SUBSCRIPTION 1,000.00") == "debit"
