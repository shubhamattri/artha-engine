"""The schedule that answers "which month can I close this, and what will it cost me then"."""

from datetime import date

from services.loan_math import compute_gold_state, forward_schedule

CAR_LOAN = dict(current_balance=800000.0, annual_rate_pct=9.35, monthly_payment=16500.0,
               start=date(2026, 8, 11))


def test_starts_from_todays_balance_not_the_original_principal():
    rows = forward_schedule(**CAR_LOAN)
    first = rows[0]
    assert first["balance"] < CAR_LOAN["current_balance"]
    assert first["interest"] == round(800000.0 * 0.0935 / 12, 2)


def test_every_row_carries_a_real_calendar_month():
    """Picking a month to foreclose in needs a month, not an index."""
    rows = forward_schedule(**CAR_LOAN)
    assert rows[0]["label"] == "Sep 2026"
    assert rows[1]["label"] == "Oct 2026"
    assert rows[0]["month"] == "2026-09-11"


def test_closing_cost_is_the_outstanding_principal_that_month():
    rows = forward_schedule(**CAR_LOAN)
    for r in rows:
        assert r["closing_cost"] == r["balance"]


def test_interest_saved_falls_to_zero_at_term():
    """Closing early saves the interest you would have paid after that point; closing at the end
    saves nothing."""
    rows = forward_schedule(**CAR_LOAN)
    assert rows[0]["interest_saved_if_closed"] > rows[-1]["interest_saved_if_closed"]
    assert rows[-1]["interest_saved_if_closed"] == 0.0
    assert rows[-1]["balance"] == 0.0


def test_a_payment_below_the_interest_never_closes():
    """Returns empty rather than looping to max_rows pretending it amortises."""
    assert forward_schedule(current_balance=1_000_000.0, annual_rate_pct=12.0,
                            monthly_payment=5_000.0, start=date(2026, 8, 11)) == []


def test_gold_loan_expected_payment_counts_without_being_logged():
    """The money leaves the account monthly. Requiring it to be logged first made the balance climb
    by the full interest every unlogged month."""
    state, extra = compute_gold_state(
        principal=997_000.0, annual_rate_pct=9.10,
        disbursement=date(2025, 8, 11), today=date(2026, 8, 11),
        payments=[], expected_monthly_payment=8_000.0)
    assert extra["principal_outstanding"] < 997_000.0     # it went DOWN, not up
    assert extra["interest_paid"] > 0
    assert state.current_balance < 997_000.0


def test_a_logged_payment_overrides_the_assumption_for_its_month():
    """That is how a part payment, a skipped month, or an irregular amount is expressed."""
    big = compute_gold_state(
        principal=997_000.0, annual_rate_pct=9.10,
        disbursement=date(2026, 2, 11), today=date(2026, 8, 11),
        payments=[{"date": "2026-05-11", "amount": 200_000.0}],
        expected_monthly_payment=8_000.0)[1]
    plain = compute_gold_state(
        principal=997_000.0, annual_rate_pct=9.10,
        disbursement=date(2026, 2, 11), today=date(2026, 8, 11),
        payments=[], expected_monthly_payment=8_000.0)[1]
    # The Rs 2L part payment replaces that month's assumed Rs 8k, so principal drops much further.
    assert big["principal_outstanding"] < plain["principal_outstanding"] - 150_000


def test_a_zero_logged_payment_expresses_a_skipped_month():
    skipped = compute_gold_state(
        principal=997_000.0, annual_rate_pct=9.10,
        disbursement=date(2026, 2, 11), today=date(2026, 8, 11),
        payments=[{"date": "2026-05-11", "amount": 0.0}],
        expected_monthly_payment=8_000.0)[1]
    paid = compute_gold_state(
        principal=997_000.0, annual_rate_pct=9.10,
        disbursement=date(2026, 2, 11), today=date(2026, 8, 11),
        payments=[], expected_monthly_payment=8_000.0)[1]
    assert skipped["principal_outstanding"] > paid["principal_outstanding"]


def test_loan_detail_actually_returns_a_schedule(db_session):
    """Reading a key the view does not publish returns 0 rows silently — which is what shipped, and
    what a passing unit test on forward_schedule() alone could never catch."""
    from datetime import date as _d

    from api.debt import get_loan_detail
    from models.household import Loan
    from models.tenant import Household

    h = Household(name="A"); db_session.add(h); db_session.commit()
    car = Loan(household_id=h.id, name="Family Car", lender="Sample Motors Finance", holder="Asha Verma",
               category="Car", original_principal=1_100_000, annual_rate_pct=9.35,
               tenure_months=60, disbursement_date=_d(2024, 4, 9))
    gold = Loan(household_id=h.id, name="Family Gold", lender="Meridian Gold Finance", holder="Parent",
                category="Gold Loan", original_principal=1_000_000, annual_rate_pct=9.10,
                tenure_months=12, disbursement_date=_d(2026, 2, 9), loan_type="gold",
                expected_monthly_payment=8_000)
    db_session.add_all([car, gold]); db_session.commit()

    for loan in (car, gold):
        v = get_loan_detail(loan.id, db=db_session, household=h)
        rows = v["forward_schedule"]
        assert rows, f"{loan.name} returned an empty schedule"
        assert rows[0]["closing_cost"] > 0

    car_rows = get_loan_detail(car.id, db=db_session, household=h)["forward_schedule"]
    assert car_rows[-1]["balance"] == 0.0          # a car loan amortises to zero
    assert car_rows[-1]["truncated"] is False

    # The gold loan needs ~390 months at Rs 8,000/mo. The row cap adapts to what the loan actually
    # needs, so the schedule runs to zero instead of stopping at a constant 360 and disagreeing with
    # months_remaining.
    gold_rows = get_loan_detail(gold.id, db=db_session, household=h)["forward_schedule"]
    assert len(gold_rows) > 360
    assert gold_rows[-1]["balance"] == 0.0
    assert gold_rows[-1]["truncated"] is False


def test_a_moratorium_loan_still_gets_a_schedule():
    """Interest-only today does NOT mean never clears. The payment steps up when the moratorium
    ends, and a schedule that gives up on month 1 hides the entire loan."""
    rows = forward_schedule(
        current_balance=1_035_000.0, annual_rate_pct=9.50,
        monthly_payment=7_000.0, start=date(2026, 8, 11),
        steps=[(1, 7_000.0), (7, 14_500.0)],     # under-servicing for 6 months, then the real EMI
    )
    assert rows, "a moratorium loan returned no schedule at all"
    assert rows[0]["principal"] == 0.0            # interest-only while in moratorium
    assert rows[0]["balance"] == 1_035_000.0      # principal untouched
    assert rows[6]["principal"] > 0               # steps up and starts amortising
    assert rows[-1]["balance"] == 0.0             # and it does clear


def test_gives_up_only_when_no_later_step_can_ever_cover_interest():
    assert forward_schedule(current_balance=1_000_000.0, annual_rate_pct=12.0,
                            monthly_payment=5_000.0, start=date(2026, 8, 11),
                            steps=[(1, 5_000.0), (13, 6_000.0)]) == []


def test_schedule_length_matches_what_the_loan_needs():
    """A constant row cap made the schedule say 360 months while months_remaining said 384."""
    long_run = forward_schedule(current_balance=1_000_000.0, annual_rate_pct=9.10,
                                monthly_payment=8_000.0, start=date(2026, 8, 11),
                                max_rows=500)
    assert len(long_run) > 360
    assert long_run[-1]["balance"] == 0.0
    assert long_run[-1]["truncated"] is False
