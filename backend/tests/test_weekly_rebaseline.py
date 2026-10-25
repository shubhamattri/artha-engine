"""Future weeks must be re-based on what is LEFT, not on the target set at the start of the cycle.

The weekly bar showed every remaining week the same untouched slice of the cycle budget, however
much had already been spent. Overspend week 1 and weeks 2-5 still each promised the original figure,
so the chart implied money that was already gone was still available to spend.

Policy, decided with the household:
  * a week that has STARTED keeps the target it was judged against — the line never moves under you
    mid-week, so an overspent bar stays visibly overspent instead of the goalposts sliding
  * weeks that have NOT started share whatever remains, in proportion to their own length
  * once nothing remains they read zero, and the size of the overshoot is stated ONCE above the
    chart rather than repeated as a negative number in every column
"""

from datetime import date

from models.tenant import Household
from models.transaction import Transaction

# A 28-day cycle divides into four clean 7-day weeks, which keeps the arithmetic in these tests
# readable: every week is worth exactly a quarter of the budget before any re-basing happens.
CYCLE_START_DAY = 1
TOTAL_TARGET = 40_000.0
FLAT_WEEKLY = 10_000.0          # TOTAL_TARGET / (28 / 7)


def _household(db_session) -> Household:
    h = Household(name="RB", cycle_start_day=CYCLE_START_DAY)
    db_session.add(h)
    db_session.commit()
    return h


def _spend(hid: int, amount: float, d: str, category: str = "Groceries") -> Transaction:
    return Transaction(household_id=hid, fingerprint=f"rb-{amount}-{d}", date=d,
                       description="Shop", merchant="Shop", source="statement", amount=amount,
                       direction="debit", category=category, txn_type="SPEND", bank="HDFC",
                       card_type="credit", card_last4="1111", stmt_month=d[:7])


def _weeks(db_session, hid: int, today: date) -> list[dict]:
    from services.cycle import weekly_breakdown
    return weekly_breakdown(db_session, today, FLAT_WEEKLY, mode="month",
                            household_id=hid, total_target=TOTAL_TARGET)


def test_overspending_week_one_shrinks_the_weeks_that_have_not_started(db_session):
    """The bug, stated as a test. Spend 18,000 of a 40,000 budget in week 1 and the remaining three
    weeks may promise only the 22,000 that is actually left, not 10,000 each."""
    h = _household(db_session)
    db_session.add(_spend(h.id, 18_000.0, "2026-02-03"))
    db_session.commit()

    weeks = _weeks(db_session, h.id, date(2026, 2, 5))          # inside week 1

    assert weeks[0]["target"] == FLAT_WEEKLY, "the week you are in keeps the line it is judged against"
    future = [w for w in weeks if w["is_future"]]
    assert len(future) == 3
    for w in future:
        assert w["target"] < FLAT_WEEKLY, "a future week may not still promise the original slice"
    assert round(sum(w["target"] for w in future), 2) == 22_000.0, "future weeks share exactly what remains"


def test_a_week_that_has_started_keeps_its_original_target(db_session):
    """Re-basing the current week would move the line you are being judged against mid-week, which
    makes overspending indistinguishable from the goalposts shifting."""
    h = _household(db_session)
    db_session.add(_spend(h.id, 30_000.0, "2026-02-02"))
    db_session.commit()

    weeks = _weeks(db_session, h.id, date(2026, 2, 4))
    assert weeks[0]["target"] == FLAT_WEEKLY
    assert weeks[0]["spent"] == 30_000.0
    assert weeks[0]["on_track"] is False                        # visibly over, and it stays that way


def test_a_completed_week_keeps_the_target_it_opened_with(db_session):
    """A past week is a record of what happened against the plan of the time. Re-basing it NOW
    would rewrite history and hide that the overspend ever occurred. (Week 1 is the case where "the
    target it opened with" and the cycle's opening pace are the same number, because nothing had
    been spent yet.)"""
    h = _household(db_session)
    db_session.add(_spend(h.id, 18_000.0, "2026-02-03"))
    db_session.commit()

    weeks = _weeks(db_session, h.id, date(2026, 2, 12))         # week 1 is now finished
    assert weeks[0]["is_current"] is False and weeks[0]["is_future"] is False
    assert weeks[0]["target"] == FLAT_WEEKLY


def test_future_targets_stay_proportional_to_bucket_length(db_session):
    """A short trailing bucket must ask for proportionally less of what REMAINS, exactly as it asked
    for proportionally less of the original budget."""
    h = Household(name="RB2", cycle_start_day=CYCLE_START_DAY)
    db_session.add(h)
    db_session.commit()
    db_session.add(_spend(h.id, 10_000.0, "2026-01-02"))        # January: 31 days = 4 weeks + 3 days
    db_session.commit()

    from services.cycle import weekly_breakdown
    weeks = weekly_breakdown(db_session, date(2026, 1, 5), FLAT_WEEKLY, mode="month",
                             household_id=h.id, total_target=TOTAL_TARGET)
    future = [w for w in weeks if w["is_future"]]
    seven_day = [w for w in future if w["end"] != weeks[-1]["end"]]
    tail = weeks[-1]
    assert round(tail["target"], 2) == round(seven_day[0]["target"] * 3 / 7, 2)
    assert round(sum(w["target"] for w in future), 2) == 30_000.0


def test_spending_past_the_whole_budget_floors_future_weeks_at_zero(db_session):
    """A negative weekly target is not a number anyone can act on, and repeating it in every column
    says the same bad news four times. Weeks read zero; the overshoot is reported once, elsewhere."""
    h = _household(db_session)
    db_session.add(_spend(h.id, 52_000.0, "2026-02-03"))        # 12,000 past a 40,000 budget
    db_session.commit()

    weeks = _weeks(db_session, h.id, date(2026, 2, 5))
    for w in weeks:
        if w["is_future"]:
            assert w["target"] == 0.0
            assert w["on_track"] is None, "a week with no budget cannot be on or off track"


def test_the_last_week_of_the_cycle_has_no_future_to_divide_into(db_session):
    """With no week left to receive it there is nothing to divide, and dividing by zero remaining
    days would take down the Plan page rather than degrade."""
    h = _household(db_session)
    db_session.add(_spend(h.id, 39_000.0, "2026-02-03"))
    db_session.commit()

    weeks = _weeks(db_session, h.id, date(2026, 2, 27))         # inside the final week
    assert not any(w["is_future"] for w in weeks)
    # Every bucket has already started, so every one carries a real, non-negative line and nothing
    # divided by an empty future. Week 1 opened the cycle on the flat pace; the later ones opened on
    # what 39,000 of spending had left them, which is why they are not all equal any more.
    assert all(w["target"] is not None and w["target"] >= 0 for w in weeks)
    assert weeks[0]["target"] == FLAT_WEEKLY
    assert weeks[-1]["target"] < FLAT_WEEKLY


def test_a_future_dated_transaction_does_not_shrink_next_week_before_it_happens(db_session):
    """Spend dated ahead of today is not money gone yet. Counting it would quietly shrink next
    week's allowance for a debit that has not left the account."""
    h = _household(db_session)
    db_session.add_all([
        _spend(h.id, 8_000.0, "2026-02-03"),                    # actually spent
        _spend(h.id, 5_000.0, "2026-02-20"),                    # dated in a future week
    ])
    db_session.commit()

    weeks = _weeks(db_session, h.id, date(2026, 2, 5))
    future = [w for w in weeks if w["is_future"]]
    # 40,000 budget - 8,000 actually spent - 2,000 still owed to the week in progress = 30,000.
    # Had the future-dated 5,000 been counted as spent this would read 25,000, which is the thing
    # this test exists to catch. (It once asserted 32,000, which quietly included the
    # current week's headroom a second time — the bug the reservation above now prevents.)
    assert round(sum(w["target"] for w in future), 2) == 30_000.0, "only the 8,000 already spent counts"


def test_without_a_total_target_the_original_flat_behaviour_is_unchanged(db_session):
    """The two existing callers in tests pass positionally and expect the flat pace. Re-basing must
    be opt-in so this function stays usable for 'what was the plan', not only 'what is left'."""
    from services.cycle import weekly_breakdown
    h = _household(db_session)
    db_session.add(_spend(h.id, 30_000.0, "2026-02-03"))
    db_session.commit()

    weeks = weekly_breakdown(db_session, date(2026, 2, 5), FLAT_WEEKLY, mode="month",
                             household_id=h.id)
    assert all(w["target"] == FLAT_WEEKLY for w in weeks)


# ── The week in progress is neither past nor future, and that gap is where the money leaked ────────
#
# Every test above puts `today` inside week 1 AND overspends week 1, so the current week's headroom
# is always zero and the double-count below is invisible. The live cycle looked different: week 1
# finished over, and the week in progress was well UNDER its target with real headroom left.

def test_the_week_in_progress_does_not_have_its_headroom_promised_twice(db_session):
    """The bug, stated as a test.

    `remaining` is the whole cycle budget minus everything spent to date, so it has to cover every
    day that has not happened yet — including the rest of the week you are standing in. But only the
    weeks that have NOT STARTED divide it up. The current week's unspent allowance is therefore
    offered twice: once as its own headroom, once inside the pool the later weeks share.

    That is spending the chart permitted and the household did not
    have.
    """
    h = _household(db_session)
    db_session.add_all([
        _spend(h.id, 18_000.0, "2026-02-03"),      # week 1 (1-7 Feb) — 8,000 over its 10,000
        _spend(h.id, 2_000.0, "2026-02-09"),       # week 2 (8-14 Feb) — the week in progress
    ])
    db_session.commit()

    weeks = _weeks(db_session, h.id, date(2026, 2, 10))        # inside week 2

    remaining = TOTAL_TARGET - 20_000.0                        # 40,000 - everything spent = 20,000
    current = next(w for w in weeks if w["is_current"])
    headroom = current["target"] - current["spent"]            # 10,000 - 2,000 = 8,000
    future_sum = round(sum(w["target"] for w in weeks if w["is_future"]), 2)

    # What the chart tells the household it may still spend, read straight off the bars.
    assert round(headroom + future_sum, 2) == remaining, (
        "the rest of the current week plus every future week must equal what is left, "
        f"not {headroom + future_sum:,.0f} against {remaining:,.0f}"
    )
    # Week 2 opened with 22,000 left over the 21 days to month end, so its line is 7,333.33 and it
    # is still owed 5,333.33 of that. The rest share what survives the reservation.
    assert current["target"] == round(22_000.0 / 21 * 7, 2)
    assert future_sum == 14_666.67
    # The point of the whole exercise: weeks 2, 3 and 4 are all on the SAME rupees-per-day.
    assert len({_rate(w) for w in weeks if w["is_current"] or w["is_future"]}) == 1


def test_an_underspent_week_in_progress_does_not_inflate_the_weeks_after_it(db_session):
    """The direction has to be right, which is the part a household would notice first.

    Overspending earlier in the cycle must make the weeks that follow SMALLER. Dividing the whole
    remaining pool among the future weeks only — while the current week still holds its own slice —
    made them larger than the flat pace they started at, so blowing week 1 appeared to earn week 3
    a raise.
    """
    h = _household(db_session)
    db_session.add_all([
        _spend(h.id, 18_000.0, "2026-02-03"),      # a real overspend in week 1
        _spend(h.id, 2_000.0, "2026-02-09"),
    ])
    db_session.commit()

    weeks = _weeks(db_session, h.id, date(2026, 2, 10))
    for w in weeks:
        if w["is_future"]:
            assert w["target"] < FLAT_WEEKLY, (
                f"week {w['week']} promises {w['target']:,.0f}, more than the {FLAT_WEEKLY:,.0f} it "
                "started with, after an overspend earlier in the cycle"
            )


def test_a_current_week_already_over_its_target_reserves_nothing(db_session):
    """A week that has already spent past its line has no headroom to reserve, so everything that is
    left belongs to the weeks after it. Without the floor this would hand them a negative
    reservation and quietly INFLATE them — the same bug, mirrored."""
    h = _household(db_session)
    db_session.add_all([
        _spend(h.id, 5_000.0, "2026-02-03"),
        _spend(h.id, 15_000.0, "2026-02-09"),      # week 2 is 5,000 past its 10,000
    ])
    db_session.commit()

    weeks = _weeks(db_session, h.id, date(2026, 2, 10))
    remaining = TOTAL_TARGET - 20_000.0
    future_sum = round(sum(w["target"] for w in weeks if w["is_future"]), 2)
    assert future_sum == remaining, "no headroom to hold back, so the future weeks share all of it"


def test_the_reservation_can_never_out_draw_what_is_left(db_session):
    """Once a week's line is set from what remained when it opened, the reservation is a FRACTION of
    that remainder — its own length over the days that were left — so it can never exceed it. This
    used to matter: with week 2 carrying the opening pace it could be owed 9,000 against a budget
    with 3,000 in it, and the cap was the only thing standing between that and negative targets in
    every column after it.

    Asserting the property rather than a scenario, because the numbers that once produced zeros no
    longer do — which is the fix working, not the guard being unnecessary."""
    h = _household(db_session)
    db_session.add_all([
        _spend(h.id, 36_000.0, "2026-02-03"),      # 26,000 over in week 1
        _spend(h.id, 1_000.0, "2026-02-09"),
    ])
    db_session.commit()

    weeks = _weeks(db_session, h.id, date(2026, 2, 10))        # only 3,000 of the budget survives
    remaining = TOTAL_TARGET - 37_000.0
    current = next(w for w in weeks if w["is_current"])
    headroom = max(0.0, current["target"] - current["spent"])
    assert headroom <= remaining, "a week cannot be owed more than the cycle has"
    assert all(w["target"] >= 0 for w in weeks), "no column may show a negative budget"
    assert round(headroom + sum(w["target"] for w in weeks if w["is_future"]), 2) == remaining


# ── The rate has to be ONE rate from here on ───────────────────────────────────────────────────────
#
# The reservation above made the buckets SUM correctly. It did not make them coherent: the week in
# progress was still carrying the flat pace computed on day 1 of the cycle, so it ran at a different
# rupees-per-day than the weeks right after it. Same cycle, same money, two speeds.

def _rate(w: dict) -> float:
    """Rupees per day a bucket is allowed, from its own target and published length."""
    return round(w["target"] / w["span"], 2)


def test_every_week_from_here_on_runs_at_the_same_daily_rate(db_session):
    """The bug a user caught by eye.

    Overspending week 1 must reduce EVERY week after it, including the one in progress. It did not:
    week 2 kept the 10,000 the cycle opened with while weeks 3 and 4 were squeezed, so the bar showed
    one household on two different daily allowances at the same time. There is only one pot of money
    left; there can only be one rate.
    """
    h = _household(db_session)
    db_session.add_all([
        _spend(h.id, 18_000.0, "2026-02-03"),      # week 1 blows its 10,000
        _spend(h.id, 2_000.0, "2026-02-09"),
    ])
    db_session.commit()

    weeks = _weeks(db_session, h.id, date(2026, 2, 10))     # inside week 2
    from_here = [w for w in weeks if w["is_current"] or w["is_future"]]
    rates = {_rate(w) for w in from_here}
    assert len(rates) == 1, f"weeks 2-4 are on different daily rates: {[(w['week'], _rate(w)) for w in from_here]}"


def test_an_overspend_lowers_the_rate_for_the_week_in_progress_too(db_session):
    """The week you are standing in is not exempt. It started AFTER the overspend happened, so the
    money it was allotted was never there — carrying the opening pace into it is the goalposts
    staying put on a pitch that has already moved."""
    h = _household(db_session)
    db_session.add(_spend(h.id, 18_000.0, "2026-02-03"))
    db_session.commit()

    weeks = _weeks(db_session, h.id, date(2026, 2, 10))     # week 2 is current, week 1 is history
    current = next(w for w in weeks if w["is_current"])
    assert current["target"] < FLAT_WEEKLY, (
        f"week 2 still promises {current['target']:,.0f} of the {FLAT_WEEKLY:,.0f} the cycle opened "
        "with, after week 1 spent 18,000 of a 40,000 budget"
    )
    # 22,000 left when week 2 began, over the 21 days from its start to the end of the month.
    assert current["target"] == round(22_000.0 / 21 * 7, 2)


def test_the_week_that_opened_the_cycle_keeps_the_opening_pace(db_session):
    """Nothing was spent before week 1, so its line IS the flat pace. The re-basing has to fall out
    to exactly that rather than being a second rule bolted alongside it."""
    h = _household(db_session)
    db_session.add(_spend(h.id, 18_000.0, "2026-02-03"))
    db_session.commit()

    weeks = _weeks(db_session, h.id, date(2026, 2, 10))
    assert weeks[0]["target"] == FLAT_WEEKLY


def test_each_finished_week_records_the_budget_it_actually_had(db_session):
    """Scanning down the bars should tell the story: as overspending accumulates the weekly line
    tightens. A past week judged against money that was never available is not a record of anything."""
    h = _household(db_session)
    db_session.add_all([
        _spend(h.id, 18_000.0, "2026-02-03"),      # week 1 over
        _spend(h.id, 12_000.0, "2026-02-09"),      # week 2 over again
    ])
    db_session.commit()

    weeks = _weeks(db_session, h.id, date(2026, 2, 17))     # week 3 is now the current one
    w1, w2, w3 = weeks[0], weeks[1], weeks[2]
    assert w1["target"] == FLAT_WEEKLY
    assert w2["target"] < w1["target"], "week 2 began with less than week 1 did"
    assert w3["target"] < w2["target"], "week 3 began with less again"


def test_a_second_overspend_squeezes_the_weeks_after_it_again(db_session):
    """It is not a one-shot correction. Blow week 2 as well and weeks 3-5 drop again, immediately,
    while week 2 is still running — because what is left has fallen and the weeks that have not
    opened yet always share what is left.

    Week 2's own line does NOT drop to match, and that is deliberate: it is the line week 2 is being
    judged against, and moving it mid-week would turn an overspent bar into a bar whose target slid
    underneath it. It stops being a claim on the money instead — its headroom floors at zero — so
    nothing it has overspent gets promised to it twice.
    """
    h = _household(db_session)
    db_session.add(_spend(h.id, 18_000.0, "2026-02-03"))       # week 1: 18,000 of a 10,000 line
    db_session.commit()

    before = _weeks(db_session, h.id, date(2026, 2, 10))       # week 2 running, nothing spent in it
    w2_line = next(w for w in before if w["is_current"])["target"]
    future_before = [w["target"] for w in before if w["is_future"]]

    db_session.add(_spend(h.id, 15_000.0, "2026-02-11"))       # week 2 blows its 7,333 line too
    db_session.commit()
    after = _weeks(db_session, h.id, date(2026, 2, 12))        # still inside week 2

    w2 = next(w for w in after if w["is_current"])
    future_after = [w["target"] for w in after if w["is_future"]]

    assert w2["target"] == w2_line, "the line week 2 is judged against must not move during week 2"
    assert w2["spent"] > w2["target"] and w2["on_track"] is False, "and it must read as overspent"
    for a, b in zip(future_after, future_before):
        assert a < b, f"week 3+ still promises {a:,.0f} after week 2 went over; it was {b:,.0f}"

    # Still exactly what is left, and still one rate across everything not yet finished.
    remaining = TOTAL_TARGET - 33_000.0
    headroom = max(0.0, w2["target"] - w2["spent"])            # zero — week 2 has no claim left
    assert headroom == 0.0
    assert round(headroom + sum(future_after), 2) == remaining
    assert len({_rate(w) for w in after if w["is_future"]}) == 1


def test_the_next_week_opens_on_what_two_overspends_left_it(db_session):
    """And when week 3 actually opens, its line is fixed at what survived both overspends — then it
    holds, so week 3 is judged against a number that was real on the day it started."""
    h = _household(db_session)
    db_session.add_all([
        _spend(h.id, 18_000.0, "2026-02-03"),                  # week 1 over
        _spend(h.id, 15_000.0, "2026-02-11"),                  # week 2 over
    ])
    db_session.commit()

    weeks = _weeks(db_session, h.id, date(2026, 2, 17))        # week 3 is now the current one
    w3 = next(w for w in weeks if w["is_current"])
    # 7,000 left when week 3 opened on 15 Feb, over the 14 days to month end.
    assert w3["target"] == round(7_000.0 / 14 * 7, 2)
    assert w3["target"] < 7_333.33, "week 3 opens on less than week 2 did"


def test_a_second_overspend_moves_every_later_week(db_session):
    """Asked directly: week 1 goes over, then week 2 goes over harder.

    A 31-day cycle with a Rs 1,24,000 budget. Week 1 spends 44,800. Week 2 then spends 41,800
    against the 23,100 it opened with. Everything after it has to move, on its own, with no one
    touching anything.
    """
    from services.cycle import weekly_breakdown
    h = Household(name="H", cycle_start_day=13)
    db_session.add(h)
    db_session.commit()
    db_session.add_all([
        _spend(h.id, 44_800.0, "2026-08-15"),                  # week 1: 13-19 Aug
        _spend(h.id, 41_800.0, "2026-08-21"),                  # week 2: 20-26 Aug
    ])
    db_session.commit()

    total = 124_000.0
    weeks = weekly_breakdown(db_session, date(2026, 8, 22), round(total * 7 / 31, 2),
                             mode="cycle", household_id=h.id, total_target=total)

    w1, w2, w3, w4, w5 = weeks
    assert w1["target"] == round(total * 7 / 31, 2)            # 28,000 — the cycle's opening pace
    assert w2["target"] == round((total - 44_800.0) / 24 * 7, 2)   # 23,100, set when week 2 opened
    # 37,400 left, shared over the 17 days of weeks 3-5, so all three land on the same rate.
    assert w3["target"] == w4["target"] == round(37_400.0 / 17 * 7, 2)      # 15,400
    assert w5["target"] == round(37_400.0 - 2 * round(37_400.0 / 17 * 7, 2), 2)   # 6,600
    assert len({_rate(w) for w in (w3, w4, w5)}) == 1

    # Nothing is promised twice: week 2 has overspent, so it holds no claim on what is left.
    assert max(0.0, w2["target"] - w2["spent"]) == 0.0
    assert round(sum(w["target"] for w in (w3, w4, w5)), 2) == total - 86_600.0
