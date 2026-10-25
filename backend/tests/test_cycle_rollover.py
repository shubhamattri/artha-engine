"""The month label and the window it resolves to must agree, on both sides of a rollover.

A cycle is labelled by the month it ENDS in. money_slice.current_key did that; api/budget resolved
the same label to the cycle whose START day fell in that month, one cycle later. They agreed only
while the live cycle happened to end in the current calendar month, and came apart the day it rolled:
on 12 Aug the app asked for "2026-09" and was handed the 12 Sep – 11 Oct window, a cycle that had not
started, so the budget read empty.
"""

from datetime import date

import pytest

from api.budget import cycle_anchor
from services import money_slice
from services.cycle import cycle_bounds

SD = 12


def _key_on(day: date) -> str:
    return money_slice.current_key("instrument_cycle", day, SD)


@pytest.mark.parametrize("day", [
    date(2026, 7, 12), date(2026, 8, 10), date(2026, 8, 11),   # inside the cycle, and its last day
    date(2026, 8, 12), date(2026, 8, 13),                       # the rollover itself
    date(2026, 9, 11), date(2026, 9, 12),                       # and the next boundary
    date(2026, 12, 31), date(2027, 1, 1),                       # across a year end
])
def test_the_label_resolves_inside_the_cycle_that_contains_the_day(day):
    anchor = cycle_anchor(_key_on(day), SD)
    cstart, cend = cycle_bounds(day, SD)
    assert cstart <= anchor <= cend, f"{day} labelled {_key_on(day)} resolved outside its own cycle"


def test_the_day_after_the_boundary_does_not_jump_a_whole_cycle():
    """The live failure, pinned."""
    assert cycle_anchor(_key_on(date(2026, 8, 12)), SD) == date(2026, 9, 11)


def test_the_anchor_is_always_the_cycles_last_day():
    """cycle_bounds returns the NEXT start day as its end, so the last day inside the cycle is one
    before it. Pinning this because the off-by-one between the two conventions is what caused the
    original bug."""
    from datetime import timedelta
    for m in ("2026-08", "2026-09", "2026-10", "2027-01"):
        a = cycle_anchor(m, SD)
        assert cycle_bounds(a, SD)[1] - timedelta(days=1) == a


def test_consecutive_labels_are_consecutive_cycles():
    a, b = cycle_anchor("2026-08", SD), cycle_anchor("2026-09", SD)
    assert (b - a).days in (28, 29, 30, 31), "labels skip a cycle"


def test_a_short_month_clamps_the_start_day():
    """A 31st cycle start has no 31 February."""
    assert cycle_anchor("2026-02", 31) == date(2026, 2, 27)
    assert cycle_anchor("2026-03", 31) == date(2026, 3, 30)
