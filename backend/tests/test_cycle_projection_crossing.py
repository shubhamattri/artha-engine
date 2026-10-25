"""The day the budget runs out, and how many days of the cycle follow it.

The Plan card states both — "BUDGET RUNS OUT 29 Aug / 15 days before the cycle ends" — so they have
to describe the same day. They did not: 29 Aug to the cycle's last day of 12 Sep is 14 days, and the
card said 15. Two roundings of one crossing, disagreeing on screen.

`_cycle_projection` had no test at all before this file.
"""

from datetime import date, timedelta

from api.budget import _cycle_projection

# A 31-day cycle, day 8.
CSTART = date(2026, 8, 13)
DAYS_TOTAL = 31
LAST_DAY = CSTART + timedelta(days=DAYS_TOTAL - 1)          # 12 Sep — the cycle's final day


def _proj(projected: float, net_spent: float = 49_000.0, total_target: float = 120_000.0,
          days_elapsed: int = 8) -> dict:
    return _cycle_projection([{"projected_inr": projected}], net_spent, total_target,
                             days_elapsed, DAYS_TOTAL, CSTART)


def test_the_run_out_date_and_the_days_after_it_describe_the_same_day():
    """The bug, stated as a test. Whatever the crossing lands on, stepping that many days forward
    from it has to reach the cycle's last day — otherwise the card contradicts itself in one line."""
    p = _proj(260_000.0)
    runs_out = date.fromisoformat(p["runs_out_on"])
    assert runs_out + timedelta(days=p["days_before_end"]) == LAST_DAY, (
        f"{p['runs_out_on']} + {p['days_before_end']} days is not {LAST_DAY.isoformat()}"
    )


def test_the_relationship_holds_wherever_the_crossing_falls():
    """Not a lucky rounding. Sweep projections from just-over-budget to wildly over and the date and
    the count must agree every time — including the ends, where the crossing sits on day 8 itself or
    on the final day."""
    for projected in (125_000.0, 150_000.0, 200_000.0, 260_000.0, 400_000.0, 1_000_000.0):
        p = _proj(projected)
        assert p["runs_out_on"], f"projection {projected:,.0f} is over budget and must name a day"
        runs_out = date.fromisoformat(p["runs_out_on"])
        assert runs_out + timedelta(days=p["days_before_end"]) == LAST_DAY, (
            f"projected {projected:,.0f}: {p['runs_out_on']} + {p['days_before_end']} "
            f"!= {LAST_DAY.isoformat()}"
        )
        assert 0 <= p["days_before_end"] < DAYS_TOTAL


def test_the_crossing_marker_sits_on_the_same_day_the_date_names():
    """`runs_out_at` is the fractional position the chart draws the dot at, and `runs_out_on` is the
    date printed beneath it. They are deliberately two precisions of one crossing, so the dot must
    land on the day the text names rather than most of a day to its left."""
    p = _proj(260_000.0)
    day_from_fraction = round(p["runs_out_at"] * DAYS_TOTAL)
    day_from_date = (date.fromisoformat(p["runs_out_on"]) - CSTART).days
    assert day_from_fraction == day_from_date


def test_a_cycle_that_holds_names_no_day_at_all():
    """A date computed past the end and displayed anyway would read as a warning about this cycle
    while describing a day that is not in it."""
    p = _proj(90_000.0)                                     # inside the 120,000 target
    assert p["runs_out_on"] is None
    assert p["days_before_end"] is None
    assert p["over_inr"] == 0.0
