"""Historical Plan correctness (the load-bearing bug): asking /budget for a past month must report
THAT month's actuals + weeks, not today's — and a past month is calendar-month only."""

from datetime import date

from models.tenant import Household
from models.transaction import Transaction


def _spend(hid, amount, d, category="Groceries", merchant="Shop"):
    return Transaction(household_id=hid, fingerprint=f"s-{amount}-{d}-{merchant}", date=d,
                       description=merchant, merchant=merchant, source="statement", amount=amount,
                       direction="debit", category=category, txn_type="SPEND", bank="HDFC",
                       card_type="credit", card_last4="1111", stmt_month=d[:7])


def test_budget_actuals_follow_requested_month_not_today(db_session):
    from api.budget import compute_budget
    h = Household(name="H"); db_session.add(h); db_session.commit()
    cur = date.today().strftime("%Y-%m")
    db_session.add_all([
        _spend(h.id, 5000.0, "2025-11-15"),                       # past month
        _spend(h.id, 9000.0, f"{cur}-01"),                        # current month
    ]); db_session.commit()

    nov = compute_budget(month="2025-11", mode="month", db=db_session, household_id=h.id)
    now = compute_budget(month=cur, mode="month", db=db_session, household_id=h.id)
    g_nov = next(i["spent_inr"] for i in nov["items"] if i["category"] == "Groceries")
    g_now = next(i["spent_inr"] for i in now["items"] if i["category"] == "Groceries")
    assert g_nov == 5000.0 and g_now == 9000.0   # month drives actuals, not the wall clock


def test_past_period_honours_the_selected_lens(db_session):
    from api.budget import compute_budget
    h = Household(name="H2"); db_session.add(h); db_session.commit()
    # The lens now fully decides history: a past month keeps whichever lens you picked (so you can
    # browse previous CYCLES cycle-by-cycle), instead of silently snapping to calendar.
    assert compute_budget(month="2025-11", mode="cycle", db=db_session, household_id=h.id)["mode"] == "cycle"
    assert compute_budget(month="2025-11", mode="month", db=db_session, household_id=h.id)["mode"] == "month"


def test_anchor_addressing_contains_and_walks_distinct_cycles(db_session):
    from api.budget import compute_budget
    h = Household(name="HA"); db_session.add(h); db_session.commit()
    may = compute_budget(anchor="2026-05-20", mode="cycle", db=db_session, household_id=h.id)
    apr = compute_budget(anchor="2026-04-20", mode="cycle", db=db_session, household_id=h.id)
    # the period CONTAINS its anchor, and stepping the anchor a month back is a DISTINCT cycle (no dead
    # step / no calendar-vs-cycle overlap).
    assert may["period_start"] <= "2026-05-20" <= may["period_end"]
    assert apr["period_start"] <= "2026-04-20" <= apr["period_end"]
    assert may["period_start"] != apr["period_start"]


def test_budget_items_carry_exact_txn_ids(db_session):
    from api.budget import compute_budget
    h = Household(name="H4"); db_session.add(h); db_session.commit()
    a = _spend(h.id, 1200.0, "2025-11-15"); b = _spend(h.id, 800.0, "2025-11-20")
    db_session.add_all([a, b]); db_session.commit()
    out = compute_budget(month="2025-11", mode="month", db=db_session, household_id=h.id)
    groc = next(i for i in out["items"] if i["category"] == "Groceries")
    assert sorted(groc["ids"]) == sorted([a.id, b.id])   # exact rows → drill reconciles with the row


def test_category_weeks_buckets_and_carries_txn_ids(db_session):
    from services.cycle import weekly_category_breakdown
    h = Household(name="H3"); db_session.add(h); db_session.commit()
    t1 = _spend(h.id, 1200.0, "2025-11-03")   # week 1
    t2 = _spend(h.id, 800.0, "2025-11-20", category="Education")  # week 3, different category
    t3 = _spend(h.id, 400.0, "2025-11-04")    # week 1, same cat as t1
    db_session.add_all([t1, t2, t3]); db_session.commit()

    grid = weekly_category_breakdown(db_session, date(2025, 11, 30), ["Groceries", "Education"],
                                     mode="month", household_id=h.id)
    # Nov = 30 days = 4 true 7-day weeks + a 2-day tail. The tail is its OWN bucket, not absorbed into
    # week 4 — the UI labels each bucket's dates and day count, so a short final stretch reads correctly.
    from datetime import date as _d
    assert len(grid["weeks"]) == 5
    days = [(_d.fromisoformat(w["end"]) - _d.fromisoformat(w["start"])).days + 1 for w in grid["weeks"]]
    assert days == [7, 7, 7, 7, 2] and sum(days) == 30        # every day of the cycle, counted once
    assert grid["weeks"][0]["start"] == "2025-11-01" and grid["weeks"][-1]["end"] == "2025-11-30"
    groc = next(c for c in grid["categories"] if c["category"] == "Groceries")
    edu = next(c for c in grid["categories"] if c["category"] == "Education")
    assert groc["weeks"][0] == 1600.0 and groc["total"] == 1600.0      # both week-1 buys summed
    assert sorted(groc["txn_ids"][0]) == sorted([t1.id, t3.id])        # exact ids for the cell
    assert edu["weeks"][2] == 800.0 and edu["txn_ids"][2] == [t2.id]   # week 3


def test_weekly_breakdown_targets_scale_to_bucket_length(db_session):
    """A short final bucket must get a proportionally SMALLER target, and the buckets must together
    account for the whole cycle budget — no day unbudgeted, no day double-counted."""
    from datetime import date as _d
    from services.cycle import weekly_breakdown
    h = Household(name="H4"); db_session.add(h); db_session.commit()
    total, days_total = 115_500.0, 30                       # November
    weekly_target = total / (days_total / 7)
    weeks = weekly_breakdown(db_session, date(2025, 11, 30), weekly_target,
                             mode="month", household_id=h.id)
    days = [(_d.fromisoformat(w["end"]) - _d.fromisoformat(w["start"])).days + 1 for w in weeks]
    assert days == [7, 7, 7, 7, 2]
    assert round(sum(w["target"] for w in weeks), 2) == total          # targets tile the whole budget
    assert weeks[-1]["target"] < weeks[0]["target"]                    # the 2-day tail asks for less
    assert round(weeks[-1]["target"], 0) == round(weekly_target * 2 / 7, 0)


def test_cycle_start_day_beyond_28_clamps_to_month_end_not_to_28(db_session):
    """A card that bills on the 31st must bill on the LAST day of short months — not silently snap
    every household to the 28th, which is what the old `min(28, start_day)` cap did."""
    from services.cycle import cycle_bounds
    # 31st anchor: Jan has 31, Feb 2026 has 28, Apr has 30 — each cycle starts on that month's real
    # last available day and the windows tile with no gap and no overlap.
    s_jan, e_jan = cycle_bounds(date(2026, 1, 31), 31)
    assert s_jan == date(2026, 1, 31) and e_jan == date(2026, 2, 28)   # Feb has no 31st
    s_feb, e_feb = cycle_bounds(date(2026, 3, 1), 31)
    assert s_feb == date(2026, 2, 28) and e_feb == date(2026, 3, 31)   # contiguous with the above
    s_apr, _ = cycle_bounds(date(2026, 4, 30), 31)
    assert s_apr == date(2026, 4, 30)                                   # April's last day
    # leap February gets the 29th, not the 28th
    s_leap, _ = cycle_bounds(date(2028, 2, 29), 31)
    assert s_leap == date(2028, 2, 29)
    # 30th anchor in February also lands on the month end
    assert cycle_bounds(date(2026, 2, 28), 30)[0] == date(2026, 2, 28)


def test_cycle_windows_tile_the_year_with_no_gap_or_overlap(db_session):
    """Every day of a year must belong to exactly one cycle, for any anchor day."""
    from datetime import timedelta
    from services.cycle import cycle_bounds
    for anchor_day in (1, 12, 28, 29, 30, 31):
        d, seen = date(2026, 1, 1), []
        while d < date(2027, 1, 1):
            s, e = cycle_bounds(d, anchor_day)
            assert s <= d < e                       # the day is inside its own window
            if not seen or seen[-1] != (s, e):
                if seen:
                    assert seen[-1][1] == s         # previous window ends exactly where this starts
                seen.append((s, e))
            d += timedelta(days=1)
        assert len(seen) >= 12


def test_cycle_start_day_is_per_household_not_global(db_session):
    """Household B must NOT inherit household A's billing day."""
    from services.cycle import household_start_day
    a = Household(name="A", cycle_start_day=12)
    b = Household(name="B", cycle_start_day=29)
    c = Household(name="C")                          # null → global fallback
    db_session.add_all([a, b, c]); db_session.commit()
    assert household_start_day(db_session, a.id) == 12
    assert household_start_day(db_session, b.id) == 29      # its own day, not A's
    from config import settings
    assert household_start_day(db_session, c.id) == settings.cycle_start_day
    assert household_start_day(None, None) == settings.cycle_start_day   # no scope → fallback


def test_weekly_buckets_follow_the_households_own_anchor(db_session):
    """The weekly breakdown must window on the household's day, so two households with different
    billing days get different buckets from the same data."""
    from datetime import date as _d
    from services.cycle import weekly_breakdown
    h = Household(name="H29", cycle_start_day=29); db_session.add(h); db_session.commit()
    weeks = weekly_breakdown(db_session, date(2026, 3, 10), 1000.0, mode="cycle", household_id=h.id)
    starts = _d.fromisoformat(weeks[0]["start"])
    assert starts == date(2026, 2, 28)               # Feb has no 29th in 2026 → clamped month end
    days = [(_d.fromisoformat(w["end"]) - _d.fromisoformat(w["start"])).days + 1 for w in weeks]
    assert sum(days) == (date(2026, 3, 29) - date(2026, 2, 28)).days
