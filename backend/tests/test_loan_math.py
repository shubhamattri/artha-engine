import os
import sys
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services import loan_math

def test_emi_matches_the_standard_formula():
    # 12,00,000 @ 12% over 60mo -> EMI 26693.34
    e = loan_math.emi(1200000, 12.0, 60)
    assert abs(e - 26693.34) < 1.0

def test_emi_zero_rate():
    assert loan_math.emi(120000, 0, 12) == 10000

def test_balance_after_reduces():
    b12 = loan_math.balance_after(1200000, 12.0, 60, 12)
    assert 1000000 < b12 < 1100000   # ~10,13,652

def test_balance_zero_at_end():
    assert loan_math.balance_after(350000, 14.8, 60, 60) == 0.0

def test_months_remaining_sane():
    e = loan_math.emi(200000, 12, 24)
    assert loan_math.months_remaining(200000, 12, e) == 24

def test_compute_state_closed():
    st = loan_math.compute_state(100000, 12, 12, date(2025,1,1), date(2026,1,1), closed=True)
    assert st.current_balance == 0 and st.months_left == 0


def test_months_left_matches_schedule_not_amortization_residual():
    # 6-month loan, 5 paid -> exactly 1 left (not 2 from rounding residual)
    from datetime import date
    st = loan_math.compute_state(140000.0, 16.0, 6, date(2025,12,29), date(2026,6,20))
    assert st.months_passed == 5
    assert st.months_left == 1
    assert 24000 < st.current_balance < 25500  # ~one final EMI

def test_prepayment_shortens_term():
    from datetime import date
    base = loan_math.compute_state(1000000, 12, 60, date(2025,1,1), date(2026,1,1))
    prepaid = loan_math.compute_state(1000000, 12, 60, date(2025,1,1), date(2026,1,1), extra_principal=200000)
    assert prepaid.current_balance < base.current_balance
    assert prepaid.months_left < base.months_left
