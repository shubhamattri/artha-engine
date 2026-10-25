"""The payoff dates assume an extra payment lands every month. Measure whether it does.

"All loans paid off by March 2031" and "done ~Mar 2029" on each loan are both conditional on the
household actually putting the planned figure at its loans. Shown as plain dates they read as facts,
and if the money stops going in the screen keeps showing them.
"""

from datetime import date

from models.loan_payment import LoanPayment
from models.tenant import Household
from services.adherence import measure


def _h(db):
    h = Household(name="A")
    db.add(h)
    db.commit()
    return h


def _pay(db, hid, when, amt):
    db.add(LoanPayment(loan_id=1, household_id=hid, date=when, amount=amt))
    db.commit()


def test_no_extra_payments_at_all_reads_as_off_plan(db_session):
    h = _h(db_session)
    r = measure(db_session, h.id, planned_extra=100_000, today=date(2026, 8, 11))
    assert r["actual_avg"] == 0.0
    assert r["status"] == "off_plan"
    assert r["adherence_pct"] == 0.0


def test_paying_the_plan_reads_as_on_plan(db_session):
    h = _h(db_session)
    for m in (5, 6, 7):
        _pay(db_session, h.id, date(2026, m, 10), 100_000)
    r = measure(db_session, h.id, planned_extra=100_000, today=date(2026, 8, 11))
    assert r["status"] == "on_plan"
    assert 95 <= r["adherence_pct"] <= 105


def test_paying_some_of_it_reads_as_behind(db_session):
    h = _h(db_session)
    for m in (5, 6, 7):
        _pay(db_session, h.id, date(2026, m, 10), 50_000)
    r = measure(db_session, h.id, planned_extra=100_000, today=date(2026, 8, 11))
    assert r["status"] == "behind"


def test_the_current_month_is_shown_but_never_read_as_a_shortfall(db_session):
    """It IS in the window now — hiding it hid the month a household actually started paying. It is
    still in progress though, so it is marked partial and the verdict is taken from the whole months.
    Counting a half-finished month against a full month's plan would report every household as behind
    on the 2nd."""
    h = _h(db_session)
    for m in (5, 6, 7):
        _pay(db_session, h.id, date(2026, m, 10), 100_000)
    _pay(db_session, h.id, date(2026, 8, 1), 0.0)      # this month, barely started
    r = measure(db_session, h.id, planned_extra=100_000, today=date(2026, 8, 11))
    assert [m["month"] for m in r["months"]] == ["2026-05", "2026-06", "2026-07", "2026-08"]
    assert r["months"][-1]["partial"] is True
    assert r["status"] == "on_plan"


def test_a_missed_month_pulls_the_average_down(db_session):
    h = _h(db_session)
    _pay(db_session, h.id, date(2026, 5, 10), 100_000)
    _pay(db_session, h.id, date(2026, 7, 10), 100_000)   # June skipped
    r = measure(db_session, h.id, planned_extra=100_000, today=date(2026, 8, 11))
    assert r["actual_avg"] == round(200_000 / 3, 2)
    assert r["status"] == "behind"
    # The whole months are what the average is taken from; the current one trails them, marked partial.
    assert [m["paid"] for m in r["months"] if not m["partial"]] == [100_000.0, 0.0, 100_000.0]


def test_a_household_not_following_the_plan_gets_the_dates_that_follow_from_reality(db_session):
    """The whole point: the same plan re-run at the observed rate, so the real dates sit beside the
    planned ones instead of only the ones that assume compliance."""
    from datetime import date as _d

    from api.debt import compute_debt
    from models.household import Loan

    h = _h(db_session)
    db_session.add(Loan(household_id=h.id, name="Car", lender="X", holder="A", category="Car",
                        original_principal=1_000_000, annual_rate_pct=9.0, tenure_months=60,
                        disbursement_date=_d(2024, 4, 9), rate_type="floating"))
    db_session.commit()
    s = compute_debt(db_session, h.id)["strategies"]
    assert "adherence" in s
    if s.get("at_actual"):
        for key, alt in s["at_actual"].items():
            planned = next(o for o in s["options"] if o["key"] == key)
            assert alt["months"] >= planned["months"], key


def test_the_month_the_household_started_paying_is_not_scored_as_failure(db_session):
    """The window used to stop at the last WHOLE month, so a household that started paying this month
    was told it was failing in the month it began succeeding. Rs 1,00,000 went in on 15 Aug against a
    plan of Rs 1,00,000 a month, and the screen said off_plan, 0%, "little or no extra is going in"."""
    h = _h(db_session)
    _pay(db_session, h.id, date(2026, 8, 15), 100_000)
    r = measure(db_session, h.id, planned_extra=100_000, today=date(2026, 8, 18))

    assert [m["month"] for m in r["months"]] == ["2026-05", "2026-06", "2026-07", "2026-08"]
    current = r["months"][-1]
    assert current["partial"] is True
    assert current["paid"] == 100_000.0
    # One part-month is not a habit. Saying so beats asserting a failure that is not true.
    assert r["status"] == "too_early"
    assert "month" in r["why"].lower()


def test_only_the_current_month_is_partial(db_session):
    h = _h(db_session)
    r = measure(db_session, h.id, planned_extra=100_000, today=date(2026, 8, 18))
    assert [m["partial"] for m in r["months"]] == [False, False, False, True]


def test_a_household_paying_nothing_anywhere_is_still_off_plan(db_session):
    """Including the current month must not turn a genuine failure into 'too early'."""
    h = _h(db_session)
    r = measure(db_session, h.id, planned_extra=100_000, today=date(2026, 8, 18))
    assert r["status"] == "off_plan"


def test_a_history_of_paying_is_judged_on_the_whole_months(db_session):
    """Once there ARE whole months to judge, they decide the verdict — a quiet first fortnight of the
    current month cannot drag a compliant household into 'behind'."""
    h = _h(db_session)
    for m in (5, 6, 7):
        _pay(db_session, h.id, date(2026, m, 10), 100_000)
    r = measure(db_session, h.id, planned_extra=100_000, today=date(2026, 8, 2))
    assert r["status"] == "on_plan"
    assert r["actual_avg"] == 100_000.0
