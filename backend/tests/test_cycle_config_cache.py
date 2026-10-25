"""The billing-cycle config must be read once per request, not once per transaction.

card_start_day() is called for every row in the ledger. Re-reading the household config each time
turned a 6,000-row ledger into ~38,000 SQL statements and 7.2s of a 7.5s page load. These tests pin
both halves: the reads collapse, and the cache stays scoped to one session so it can never serve one
household's billing day to another.
"""

from datetime import date

from models.tenant import Household
from services import cycle


def _count_queries(db, fn):
    """Run fn and count SQL statements actually issued."""
    from sqlalchemy import event

    n = {"c": 0}

    def before(conn, cur, stmt, params, ctx, many):  # noqa: ANN001, PLR0913
        n["c"] += 1

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", before)
    try:
        fn()
    finally:
        event.remove(engine, "before_cursor_execute", before)
    return n["c"]


def test_config_is_read_once_no_matter_how_many_rows(db_session):
    h = Household(name="H")
    db_session.add(h)
    db_session.commit()
    today = date(2026, 8, 4)

    # Warm the first read, then measure 500 more calls — they must cost nothing.
    cycle.card_start_day("HDFC", "Platinum", db_session, h.id)
    n = _count_queries(db_session, lambda: [
        cycle.card_start_day("HDFC", "Platinum", db_session, h.id) for _ in range(500)
    ])
    assert n == 0, f"500 calls issued {n} queries — the per-request cache is not holding"

    n2 = _count_queries(db_session, lambda: [
        cycle.in_current_cycle("2026-08-01", "HDFC", "Platinum", today, db_session, h.id)
        for _ in range(500)
    ])
    assert n2 == 0, f"in_current_cycle issued {n2} queries across 500 rows"


def test_two_households_do_not_share_a_cached_billing_day(db_session):
    """The cache key includes the household. A leak here would window one household's whole ledger
    with another's billing day — silently wrong numbers everywhere."""
    a = Household(name="A", cycle_start_day=5)
    b = Household(name="B", cycle_start_day=20)
    db_session.add_all([a, b])
    db_session.commit()

    assert cycle.household_start_day(db_session, a.id) == 5
    assert cycle.household_start_day(db_session, b.id) == 20
    # again, now that both are cached
    assert cycle.household_start_day(db_session, a.id) == 5
    assert cycle.household_start_day(db_session, b.id) == 20


def test_a_fresh_session_sees_a_changed_billing_day(db_session):
    """Cache is per-session, so the next request picks up an edit — it must not go stale for good."""
    h = Household(name="H", cycle_start_day=5)
    db_session.add(h)
    db_session.commit()
    assert cycle.household_start_day(db_session, h.id) == 5

    h.cycle_start_day = 15
    db_session.commit()
    db_session.info.pop("_artha_cycle_cache", None)      # what a new request gets
    assert cycle.household_start_day(db_session, h.id) == 15


def test_no_db_session_still_works(db_session):
    """Callers without a session (there are several) must not blow up on the cache path."""
    assert isinstance(cycle.household_start_day(None, None), int)
    assert isinstance(cycle.card_start_day("HDFC", "Platinum", None, None), int)
