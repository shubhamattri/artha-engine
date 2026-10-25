"""A prepaid loan must report the interest it actually paid, not its full schedule.

The closed branch returned months_passed = tenure and interest_paid = the whole life's interest.
That describes a loan that ran to term. These were PREPAID, so it credited interest that was
specifically avoided, which is the entire reason for prepaying. The UI also printed the tenure as
elapsed time, so a loan closed after two years read as seven.
"""

from datetime import date

from services.loan_math import balance_after, compute_state, emi


def _loan():
    """Rs 3,00,000 at 11% over 60 months, prepaid and closed after 20 months."""
    return dict(principal=300000.0, annual_rate_pct=11.0, tenure_months=60,
                disbursement=date(2024, 1, 15), today=date(2026, 6, 1))


def test_months_passed_is_the_life_of_the_loan_not_its_tenure():
    a = _loan()
    st = compute_state(a["principal"], a["annual_rate_pct"], a["tenure_months"],
                       a["disbursement"], a["today"], closed=True, closed_on=date(2025, 9, 15))
    assert st.months_passed == 20, f"reported {st.months_passed} months for a loan that ran 20"
    assert st.months_passed != a["tenure_months"], "still reporting the tenure as elapsed"


def test_interest_paid_excludes_what_prepaying_avoided():
    a = _loan()
    st = compute_state(a["principal"], a["annual_rate_pct"], a["tenure_months"],
                       a["disbursement"], a["today"], closed=True, closed_on=date(2025, 9, 15))
    e = emi(a["principal"], a["annual_rate_pct"], a["tenure_months"])
    expected = e * 20 - (a["principal"] - balance_after(
        a["principal"], a["annual_rate_pct"], a["tenure_months"], 20))
    assert abs(st.interest_paid - expected) < 1.0
    assert st.interest_paid < st.total_interest_over_life, "prepaying saved nothing"
    # The figure the app used to show.
    assert st.interest_paid < st.total_interest_over_life * 0.6, "still close to the full-term interest"


def test_a_loan_that_ran_to_term_is_unchanged():
    """The control: when a loan genuinely completes its tenure, full interest IS what was paid."""
    st = compute_state(300000.0, 11.0, 60, date(2024, 1, 15), date(2029, 3, 1),
                       closed=True, closed_on=date(2029, 1, 15))
    assert st.months_passed == 60
    assert abs(st.interest_paid - st.total_interest_over_life) < 1.0


def test_a_closed_loan_still_reads_as_finished():
    """Balance zero, nothing left, 100% — the closed semantics must survive the fix."""
    st = compute_state(300000.0, 11.0, 60, date(2024, 1, 15), date(2026, 6, 1),
                       closed=True, closed_on=date(2025, 9, 15))
    assert st.current_balance == 0.0
    assert st.months_left == 0
    assert st.progress_pct == 100.0
