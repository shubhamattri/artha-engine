"""The date repair may only move a row forward by exactly one day.

Setting every SMS row to its message's arrival date looks equivalent and is not. Banks send EMI and
bill reminders in ADVANCE -- "EMI of Rs 12345 is due on 07-Jul-2026" arrives on 30 June -- so the
arrival time is not the transaction date. Applying the broad rule moved 14 rows backward by 2 to 7
days and had to be undone from a backup. Forward-by-one is the UTC bug's signature; nothing else is.
"""

from datetime import date


def _should_fix(stored: date, arrived: date) -> bool:
    return (arrived - stored).days == 1


def test_the_midnight_case_is_corrected():
    """00:33 IST on the 13th stored as the 12th — one day forward, the actual bug."""
    assert _should_fix(date(2026, 8, 12), date(2026, 8, 13))


def test_an_advance_reminder_is_left_alone():
    """'due on 07-Jul' arriving 30 Jun. The stated date is right; arrival is 7 days early."""
    assert not _should_fix(date(2026, 7, 7), date(2026, 6, 30))


def test_a_delayed_alert_is_left_alone():
    """An alert arriving days late must not drag the transaction forward to match it."""
    assert not _should_fix(date(2026, 7, 1), date(2026, 7, 5))


def test_an_already_correct_row_is_left_alone():
    assert not _should_fix(date(2026, 8, 13), date(2026, 8, 13))
