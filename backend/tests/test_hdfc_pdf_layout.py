"""HDFC emails PDFs and only exports CSV on request, so nine statements were unreadable.

The dispatch table listed HDFC as .csv-only, so every HDFC PDF was rejected before a parser ever
saw it. The rows were plain text the whole time. Three details decide whether the money lands on
the right side of the ledger, and each is pinned below.
"""

from parsers.hdfc_pdf import _parse_lines

LINES = [
    "Domestic Transactions",
    "DATE & TIME TRANSACTION DESCRIPTION REWARDS AMOUNT PI",
    "ASHA VERMA S",
    "21/04/2026| 11:09 EXAMPLE MUSIC SIMUMBAI C 299.00 l",
    "25/04/2026| 15:56 EXAMPLE SERVICESGURGAON + 12 C 543.00 l",
    "CREDIT CARD PAYMENTNet Banking (Ref#",
    "22/04/2026| 07:36 + C 45,000.00 l",
    "00000000000111022233344)",
    "Page 1 of 4",
    "International Transactions",
    "DATE & TIME TRANSACTION DESCRIPTION REWARDS AMOUNT PI",
    "11/05/2026 | 17:42 EXAMPLECLOUD USD 12.34 + 28 C 1,050.00 l",
]


def _rows():
    return _parse_lines(LINES, "Credit Card", "7788")


def test_it_reads_the_pdf_layout_at_all():
    assert len(_rows()) == 4


def test_bare_plus_before_the_amount_is_a_credit():
    r = next(x for x in _rows() if "CREDIT CARD PAYMENT" in x.description)
    assert r.direction == "credit"
    assert r.amount == 45000.00


def test_plus_followed_by_a_number_is_reward_points_not_a_credit():
    """The rewards column and the credit marker are the same '+' character. Reading the rewards
    variant as a credit flips a purchase into money coming in."""
    r = next(x for x in _rows() if "EXAMPLE SERVICES" in x.description)
    assert r.direction == "debit"
    assert r.amount == 543.00
    assert "12" not in r.description


def test_international_rows_have_a_space_before_the_pipe():
    """Missing this section under-reported one statement's debits by 3,500.00 while its credits
    still reconciled, so only the debit side was wrong and the totals looked plausible."""
    r = next(x for x in _rows() if "EXAMPLECLOUD" in x.description)
    assert r.direction == "debit"
    assert r.amount == 1050.00


def test_wrapped_descriptions_are_stitched_from_neighbouring_lines():
    r = next(x for x in _rows() if x.amount == 45000.00)
    assert "CREDIT CARD PAYMENT" in r.description


def test_dates_become_iso():
    r = next(x for x in _rows() if "EXAMPLE MUSIC" in x.description)
    assert r.date == "2026-04-21"
    assert r.stmt_month == "2026-04"
