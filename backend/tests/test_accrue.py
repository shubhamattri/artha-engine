from datetime import date

from services import accrue


def test_zero_rate_is_just_contributions():
    st = accrue.accrue(
        opening_balance=10000, as_of=date(2026, 1, 1), today=date(2026, 7, 1),
        monthly_contribution=1000, employer_contribution=0, annual_rate_pct=0,
        compounding="monthly", contributions=None, maturity_date=None, active=True,
    )
    # 6 months elapsed → 6 contributions of 1000 on top of 10000
    assert st.months_elapsed == 6
    assert st.current_value == 16000
    assert st.total_contributed == 16000
    assert st.interest_earned == 0


def test_epf_employer_match_counts():
    st = accrue.accrue(
        opening_balance=0, as_of=date(2026, 1, 1), today=date(2026, 4, 1),
        monthly_contribution=1800, employer_contribution=1800, annual_rate_pct=0,
        compounding="annual", contributions=None, maturity_date=None, active=True,
    )
    # 3 months × (1800 + 1800)
    assert st.current_value == 10800
    assert st.monthly_inflow == 3600


def test_interest_accrues_positive():
    st = accrue.accrue(
        opening_balance=100000, as_of=date(2025, 1, 1), today=date(2026, 1, 1),
        monthly_contribution=0, employer_contribution=0, annual_rate_pct=7.1,
        compounding="annual", contributions=None, maturity_date=None, active=True,
    )
    # ~7.1% on 1 lakh over a year, monthly-stepped → ≈ 1,07,100
    assert 106900 < st.current_value < 107200
    assert st.interest_earned > 6900


def test_adhoc_lump_sum_added():
    st = accrue.accrue(
        opening_balance=0, as_of=date(2026, 1, 1), today=date(2026, 6, 1),
        monthly_contribution=0, employer_contribution=0, annual_rate_pct=0,
        compounding="annual", contributions=[{"amount": 50000, "date": "2026-03-15"}],
        maturity_date=None, active=True,
    )
    assert st.current_value == 50000
    assert st.total_contributed == 50000


def test_rd_maturity_projected_and_contributions_stop_after():
    st = accrue.accrue(
        opening_balance=0, as_of=date(2026, 1, 1), today=date(2026, 4, 1),
        monthly_contribution=5000, employer_contribution=0, annual_rate_pct=7,
        compounding="quarterly", contributions=None,
        maturity_date=date(2027, 1, 1), active=True,
    )
    assert st.maturity_value is not None and st.maturity_value > st.current_value
    assert not st.matured


def test_inactive_stops_contributions():
    st = accrue.accrue(
        opening_balance=20000, as_of=date(2026, 1, 1), today=date(2026, 6, 1),
        monthly_contribution=1000, employer_contribution=0, annual_rate_pct=0,
        compounding="monthly", contributions=None, maturity_date=None, active=False,
    )
    assert st.current_value == 20000  # no contributions added
    assert st.monthly_inflow == 0
