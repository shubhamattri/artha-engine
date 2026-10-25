"""The year has to be twelve periods, contiguous, and equal to the Plan.

Every bug this file pins was found by running the endpoint against a real ledger rather than by
reading it, and each one produced a plausible-looking number:

  * a whole cycle fell out of the annual total, because the current calendar month is addressed as
    "today" and today sits inside the cycle labelled by the NEXT month. Two labels pointed at one
    period and one period had no label at all.
  * a future month reporting 0 instead of null reads as "we budgeted nothing and spent nothing",
    which is a statement about a month that has not happened.
"""

from datetime import date, timedelta

from api.annual import compute_year
from models.household import Budget
from models.tenant import Household
from models.transaction import Transaction

CYCLE_START = 13


def _household(db_session, year_start_month: int = 1) -> Household:
    """Most cases below pin a JANUARY year, so the arithmetic under test is readable. April is the
    shipped default and gets its own tests — the point of the parameter is that neither month is
    baked into the code."""
    h = Household(name="YEAR", cycle_start_day=CYCLE_START, year_start_month=year_start_month)
    db_session.add(h)
    db_session.commit()
    return h


def _target(db_session, hid: int, month: str, amount: float, category: str = "Groceries"):
    db_session.add(Budget(household_id=hid, category=category, month=month,
                          monthly_limit_inr=amount))
    db_session.commit()


def _spend(db_session, hid: int, when: str, amount: float, category: str = "Groceries",
           description: str = "Shop"):
    t = Transaction(household_id=hid, fingerprint=f"yr-{when}-{amount}-{description}", date=when,
                    description=description, merchant=description, source="statement",
                    amount=amount, direction="debit", category=category, txn_type="SPEND",
                    bank="TESTBANK", card_type="account", card_last4="1111", stmt_month=when[:7])
    db_session.add(t)
    db_session.commit()
    return t


def test_the_year_is_twelve_contiguous_periods_with_nothing_missing(db_session):
    """The regression. A year that skips a period understates itself by a whole month and looks
    completely normal doing it."""
    h = _household(db_session)
    _target(db_session, h.id, "2026-01", 100.0)

    y = compute_year(2026, db_session, h.id, today=date(2026, 8, 18))
    assert len(y["months"]) == 12

    for prev, nxt in zip(y["months"], y["months"][1:]):
        gap = date.fromisoformat(nxt["window"]["start"]) - date.fromisoformat(prev["window"]["end"])
        assert gap == timedelta(days=1), f"{prev['month']} → {nxt['month']} is not contiguous"

    labels = [m["month"] for m in y["months"]]
    assert len(set(labels)) == 12, "a label was used twice"


def test_the_live_period_is_the_one_containing_today(db_session):
    h = _household(db_session)
    _target(db_session, h.id, "2026-01", 100.0)
    y = compute_year(2026, db_session, h.id, today=date(2026, 8, 18))

    partial = [m for m in y["months"] if m["state"] == "partial"]
    assert len(partial) == 1
    w = partial[0]["window"]
    assert w["start"] <= "2026-08-18" <= w["end"]
    assert [m["state"] for m in y["months"]].count("closed") == 8


def test_a_period_that_has_not_started_reports_nothing_not_zero(db_session):
    """None means "has not happened"; 0.0 means "happened, and cost nothing". Collapsing the two
    puts months of imaginary underspend into the year."""
    h = _household(db_session)
    _target(db_session, h.id, "2026-01", 100.0)
    y = compute_year(2026, db_session, h.id, today=date(2026, 3, 5))

    future = [m for m in y["months"] if m["state"] == "future"]
    assert future, "March should leave later periods in the future"
    assert all(m["net_spent_inr"] is None for m in future)
    assert all(m["cumulative_inr"] is None for m in future)
    assert all(m["target_inr"] > 0 for m in future), "a future period still has a target"


def test_every_period_equals_what_the_plan_reports_for_it(db_session):
    """api/budget OWNS actual spend. If these two ever disagree the household is shown two different
    truths on two tabs, and neither screen can tell which one is wrong."""
    from api.budget import compute_budget
    h = _household(db_session)
    _target(db_session, h.id, "2026-01", 50_000.0)
    _spend(db_session, h.id, "2026-01-20", 4_000.0)
    _spend(db_session, h.id, "2026-02-02", 7_500.0)
    _spend(db_session, h.id, "2026-03-14", 1_200.0)

    y = compute_year(2026, db_session, h.id, today=date(2026, 4, 1))
    for m in y["months"]:
        if m["state"] == "future":
            continue
        owned = compute_budget(anchor=m["window"]["end"], mode="cycle", db=db_session,
                               household_id=h.id)
        assert m["net_spent_inr"] == owned["net_spent_inr"], f"{m['month']} disagrees with the Plan"


def test_the_envelope_is_the_twelve_targets_and_a_month_never_moves(db_session):
    """A yearly view that quietly rolls an underspend into next month's allowance would make the
    monthly target meaningless. The year only reports; it never rewrites a month."""
    h = _household(db_session)
    _target(db_session, h.id, "2026-01", 40_000.0)
    _target(db_session, h.id, "2026-01", 10_000.0, category="Travel")
    before = {(b.month, b.category, b.monthly_limit_inr) for b in db_session.query(Budget).all()}

    y = compute_year(2026, db_session, h.id, today=date(2026, 8, 18))
    assert y["envelope_inr"] == 50_000.0 * 12, "later months inherit January's plan"
    assert all(m["target_inr"] == 50_000.0 for m in y["months"])

    after = {(b.month, b.category, b.monthly_limit_inr) for b in db_session.query(Budget).all()}
    assert after == before, "reading the year wrote to the budget"


def test_a_month_with_its_own_target_is_not_overwritten_by_the_carried_one(db_session):
    h = _household(db_session)
    _target(db_session, h.id, "2026-01", 40_000.0)
    _target(db_session, h.id, "2026-06", 33_000.0)

    y = compute_year(2026, db_session, h.id, today=date(2026, 8, 18))
    by_month = {m["month"]: m["target_inr"] for m in y["months"]}
    assert by_month["2026-05"] == 40_000.0
    assert by_month["2026-06"] == 33_000.0
    assert by_month["2026-07"] == 33_000.0, "the most recent month set is the one that carries"


def test_it_names_the_transaction_that_used_up_the_year(db_session):
    """The one fact on this screen that needs no threshold, no average and no interpretation."""
    h = _household(db_session)
    _target(db_session, h.id, "2026-01", 10_000.0)          # envelope = 1,20,000
    _spend(db_session, h.id, "2026-01-20", 90_000.0, description="Sofa")
    _spend(db_session, h.id, "2026-02-02", 25_000.0, description="Fridge")
    _spend(db_session, h.id, "2026-02-20", 40_000.0, description="Car service")

    y = compute_year(2026, db_session, h.id, today=date(2026, 4, 1))
    assert y["envelope_inr"] == 120_000.0
    assert y["exhausted"]["description"] == "Car service", "the one that crossed the line"
    assert y["exhausted"]["date"] == "2026-02-20"


def test_a_year_still_inside_its_envelope_says_so_rather_than_guessing(db_session):
    h = _household(db_session)
    _target(db_session, h.id, "2026-01", 10_000.0)
    _spend(db_session, h.id, "2026-01-20", 5_000.0)

    y = compute_year(2026, db_session, h.id, today=date(2026, 4, 1))
    assert y["exhausted"] is None
    assert y["outlook"]["breach"] is False


def test_the_rate_ignores_the_live_period(db_session):
    """A cycle three days old would drag the average down and report every breach as averted right
    up to the moment it lands."""
    h = _household(db_session)
    _target(db_session, h.id, "2026-01", 10_000.0)
    _spend(db_session, h.id, "2026-01-20", 60_000.0)        # inside the closed 2026-02 cycle
    _spend(db_session, h.id, "2026-02-14", 1_000.0)         # inside the live 2026-03 cycle

    y = compute_year(2026, db_session, h.id, today=date(2026, 2, 20))
    closed = [m for m in y["months"] if m["state"] == "closed"]
    assert y["outlook"]["months_closed"] == len(closed)
    assert y["outlook"]["rate_inr"] == round(
        sum(m["net_spent_inr"] for m in closed) / len(closed), 2)
    assert 1_000.0 not in [m["net_spent_inr"] for m in closed]


def test_the_biggest_purchases_come_back_named_and_in_order(db_session):
    """The screen's whole premise: a household recognises its own car dealer by name, which is
    something no statistic about the amount can convey."""
    h = _household(db_session)
    _target(db_session, h.id, "2026-01", 10_000.0)
    _spend(db_session, h.id, "2026-01-20", 3_000.0, description="Coffee run")
    _spend(db_session, h.id, "2026-01-21", 90_000.0, description="Car dealer")
    _spend(db_session, h.id, "2026-02-02", 45_000.0, description="Jeweller")

    y = compute_year(2026, db_session, h.id, today=date(2026, 4, 1))
    names = [p["description"] for p in y["top_purchases"]]
    assert names[:2] == ["Car dealer", "Jeweller"]
    assert y["top_purchases"][0]["amount_inr"] == 90_000.0
    assert y["purchase_count"] == 3


def test_within_and_above_always_add_back_up_to_what_was_spent(db_session):
    """The whole point of this split: it needs no threshold, so it cannot be argued with. If the two
    halves ever stop summing to the month's spend, one of them is being computed against a different
    window and the screen is quietly double-counting or losing money."""
    h = _household(db_session)
    _target(db_session, h.id, "2026-01", 10_000.0)
    _spend(db_session, h.id, "2026-01-20", 25_000.0)        # a month well over
    _spend(db_session, h.id, "2026-02-20", 4_000.0)         # a month well under

    y = compute_year(2026, db_session, h.id, today=date(2026, 4, 1))
    for m in y["months"]:
        if m["state"] == "future":
            continue
        assert round(m["within_target_inr"] + m["above_target_inr"], 2) == m["net_spent_inr"], \
            f"{m['month']} does not add back up"
    assert round(y["within_target_inr"] + y["above_target_inr"], 2) == y["net_spent_inr"]


def test_an_under_month_never_absorbs_an_over_month(db_session):
    """Judged per month, deliberately. A year-level min/max would let January's underspend pay for
    April's car, which is exactly what having monthly targets is meant to prevent — and it would
    report a household as far closer to plan than it is."""
    h = _household(db_session)
    _target(db_session, h.id, "2026-01", 10_000.0)
    _spend(db_session, h.id, "2026-01-20", 30_000.0)        # 20,000 over its month
    _spend(db_session, h.id, "2026-02-20", 1_000.0)         # 9,000 under its month

    y = compute_year(2026, db_session, h.id, today=date(2026, 4, 1))
    assert y["above_target_inr"] == 20_000.0, "the under month did not cancel the over month"
    assert y["within_target_inr"] == 11_000.0


def test_with_no_target_every_rupee_counts_as_above(db_session):
    """A month nobody budgeted has no line to be inside of. Calling that spend "within target" would
    invent a budget that was never set."""
    h = _household(db_session)
    _spend(db_session, h.id, "2026-01-20", 7_000.0)

    y = compute_year(2026, db_session, h.id, today=date(2026, 4, 1))
    assert y["within_target_inr"] == 0.0
    assert y["above_target_inr"] == 7_000.0


def test_it_counts_the_periods_that_had_no_target_at_all(db_session):
    """Without this the screen cannot tell "this year was blown" apart from "we had not started
    budgeting yet" — an unbudgeted period puts ALL its spend in the over column, so an early year
    reads as a catastrophe whose real cause is an empty budget table."""
    h = _household(db_session)
    _spend(db_session, h.id, "2026-01-20", 7_000.0)       # no target anywhere → all periods bare
    y = compute_year(2026, db_session, h.id, today=date(2026, 4, 1))
    seen = [m for m in y["months"] if m["state"] != "future"]
    assert y["unbudgeted_periods"] == len(seen)

    _target(db_session, h.id, "2026-01", 10_000.0)        # from January onward there IS a line
    y2 = compute_year(2026, db_session, h.id, today=date(2026, 4, 1))
    assert y2["unbudgeted_periods"] == 0


# --- the financial year, which is April here and January elsewhere ------------------

def test_a_household_with_no_setting_gets_an_april_year(db_session):
    """The shipped default. India files April to March, and a January-based view splits every real
    annual figure across two of them."""
    from models.tenant import Household
    h = Household(name="FY", cycle_start_day=CYCLE_START)     # year_start_month left null
    db_session.add(h)
    db_session.commit()

    y = compute_year(2026, db_session, h.id, today=date(2026, 8, 18))
    assert y["start_month"] == 4
    assert [m["month"] for m in y["months"]][0] == "2026-04"
    assert [m["month"] for m in y["months"]][-1] == "2027-03"
    assert y["label"] == "2026–27", "a year that straddles two says so"


def test_january_to_march_belongs_to_the_year_before_it(db_session):
    """The whole reason for the change. On an April year, February 2026 is in the 2025 year, and
    calling it 2026 is what was splitting annual figures in half."""
    h = _household(db_session, year_start_month=4)
    _target(db_session, h.id, "2025-04", 10_000.0)
    _spend(db_session, h.id, "2026-02-10", 8_000.0, description="February spend")

    prev = compute_year(2025, db_session, h.id, today=date(2026, 8, 18))
    assert "2026-02" in [m["month"] for m in prev["months"]]
    assert 8_000.0 in [m["net_spent_inr"] for m in prev["months"] if m["net_spent_inr"]]

    cur = compute_year(2026, db_session, h.id, today=date(2026, 8, 18))
    assert "2026-02" not in [m["month"] for m in cur["months"]]


def test_asking_for_no_year_gives_the_one_today_is_in(db_session):
    """In March, an April-year household is still in LAST calendar year's financial year. Defaulting
    to date.today().year would show them an empty year that has not started."""
    h = _household(db_session, year_start_month=4)
    _target(db_session, h.id, "2025-04", 10_000.0)

    assert compute_year(None, db_session, h.id, today=date(2026, 3, 20))["year"] == 2025
    assert compute_year(None, db_session, h.id, today=date(2026, 4, 1))["year"] == 2026


def test_a_january_household_still_gets_a_plain_calendar_year(db_session):
    """The month is the household's, not the code's. Nothing about April may be baked in."""
    h = _household(db_session, year_start_month=1)
    y = compute_year(2026, db_session, h.id, today=date(2026, 8, 18))
    assert [m["month"] for m in y["months"]][0] == "2026-01"
    assert [m["month"] for m in y["months"]][-1] == "2026-12"
    assert y["label"] == "2026", "a year inside one calendar year does not need a range"


def test_the_month_lens_dates_each_period_from_its_own_label(db_session):
    """An April year runs into the next calendar year, so its last three periods are 2027-01..03.
    Dating them from the year the view is NAMED after reported January 2026 three times."""
    h = _household(db_session, year_start_month=4)
    _target(db_session, h.id, "2026-04", 10_000.0)

    y = compute_year(2026, db_session, h.id, mode="month", today=date(2026, 8, 18))
    tail = y["months"][-3:]
    assert [m["month"] for m in tail] == ["2027-01", "2027-02", "2027-03"]
    assert [m["window"]["start"] for m in tail] == ["2027-01-01", "2027-02-01", "2027-03-01"]


def test_a_household_with_no_budget_at_all_does_not_invent_an_envelope(db_session):
    """Zero envelope means nobody has planned yet. Reporting a breach against it would be a verdict
    on a decision that was never made."""
    h = _household(db_session)
    _spend(db_session, h.id, "2026-01-20", 9_000.0)

    y = compute_year(2026, db_session, h.id, today=date(2026, 4, 1))
    assert y["envelope_inr"] == 0.0
    assert y["exhausted"] is None
    assert y["outlook"]["breach"] is False
