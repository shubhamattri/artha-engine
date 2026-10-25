"""A redesigned statement pushed the issuer's name past the 400-character header window.

Kotak's 2026 layout leads with the cardholder's name and full postal address, so "Kotak" first
appears well past the window the detector reads. Detection returned no bank, no parser was selected,
and a statement that opens and parses fine was refused as an unreadable format.

The narrow window is deliberate — a transaction body is full of other banks' UPI handles, and
reading the whole document would mis-route an Axis statement to HDFC. So the fallback only runs when
the header found nothing, and only accepts an outright winner.
"""

from parsers.detect import detect_statement


def _detect(text: str, filename: str = "statement.pdf"):
    """detect_statement sniffs the file itself; feed text by faking the sniff."""
    import parsers.detect as d

    original = d._sniff_text
    d._sniff_text = lambda *a, **k: text
    try:
        return detect_statement("/nonexistent.pdf", ".pdf", filename)
    finally:
        d._sniff_text = original


# Synthetic fixture modelled on the real layout: cardholder line + a postal address long enough to
# push the issuer's name past the 400-character header window.
LONG_ADDRESS = "Asha Verma . Monthly statement for your Classic Credit Card X4321 " + "Flat No 202 D Block Rosewood Heights Sampletown 560001 " * 8


def test_issuer_past_the_header_window_is_still_found():
    assert _detect(LONG_ADDRESS + " Kotak Mahindra Bank Card Variant Kotak fees Kotak").bank == "kotak"


def test_the_header_window_still_wins_when_it_matches():
    assert _detect("HDFC Bank Credit Card Statement" + " icici " * 20).bank == "hdfc"


def test_an_incidental_mention_does_not_decide_a_tie():
    """Two banks named equally often is not evidence of an issuer, so refuse rather than guess."""
    assert _detect(LONG_ADDRESS + " kotak paid to icici ").bank is None


def test_no_bank_anywhere_stays_none():
    assert _detect("Some unrelated document with no issuer named").bank is None
