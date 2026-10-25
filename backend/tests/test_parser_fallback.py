"""An unsupported statement format must not read as "this card cannot be tracked".

It can: SMS capture works for any bank once the card is registered, and Excel/CSV imports today. The
refusal message is the only place the household learns that, so it has to say it.
"""

from services.ingest import select_parser


def test_unknown_bank_pdf_points_at_the_two_things_that_do_work():
    parser, err = select_parser("fictionalbank", ".pdf")
    assert parser is None
    assert err
    low = err.lower()
    assert "excel" in low or "csv" in low          # the import that works right now
    assert "accounts" in low and "settings" in low  # the tracking that works right now
    # It must not imply the card is untrackable.
    assert "not supported" not in low


def test_unknown_bank_excel_just_works():
    """The generic account parser is the fallback — a new bank's Excel needs no code."""
    parser, err = select_parser("fictionalbank", ".xlsx")
    assert err is None
    assert parser is not None


def test_known_bank_wrong_extension_still_names_the_right_file():
    parser, err = select_parser("kotak", ".csv")   # kotak has a .pdf reader only
    assert parser is None
    assert ".pdf" in err


def test_a_bank_specific_reader_still_wins_over_the_generic_one():
    """ICICI .xlsx must use ICICI's own credit-card reader, not the generic account parser."""
    icici, _ = select_parser("icici", ".xlsx")
    generic, _ = select_parser("fictionalbank", ".xlsx")
    assert icici is not generic
