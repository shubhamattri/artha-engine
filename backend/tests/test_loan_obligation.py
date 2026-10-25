"""One shared loan-obligation truth — a flexi/moratorium loan (ICICI Education style) must report the
SAME EMI on Debt, Cashflow and Commitments. Before this, each computed it differently."""

from datetime import date

from models.household import Loan
from models.tenant import Household
from services import loan_math, loan_obligation
from services.commitments import list_commitments


def _flexi_loan(hid: int) -> Loan:
    # ₹20L sanctioned, ₹10L drawn, 24-month moratorium (interest-only), 10%/yr, 120-month tenure.
    return Loan(household_id=hid, name="ICICI Education", lender="ICICI", holder="Household",
                category="Education", original_principal=2000000.0, annual_rate_pct=10.0, tenure_months=120,
                disbursement_date=date(2026, 1, 1), loan_type="flexi",
                flexi_terms={"disbursed_amount": 1000000.0, "moratorium_months": 24, "interest_mode": "full"})


def test_flexi_moratorium_emi_is_not_the_plain_amortization_figure(db_session):
    h = Household(name="HL"); db_session.add(h); db_session.commit()
    loan = _flexi_loan(h.id); db_session.add(loan); db_session.commit()

    emi = loan_obligation.monthly_emi(loan, date(2026, 6, 1))          # still inside the moratorium
    full_amortized = loan_math.emi(2000000.0, 10.0, 120)              # the WRONG number old code used
    assert emi > 0
    assert round(emi) != round(full_amortized)                        # moratorium EMI ≠ full EMI


def test_commitments_uses_the_shared_loan_emi(db_session):
    h = Household(name="HL2"); db_session.add(h); db_session.commit()
    loan = _flexi_loan(h.id); db_session.add(loan); db_session.commit()

    row = next(c for c in list_commitments(db_session, h.id) if c["ref"] == f"loan:{loan.id}")
    # Commitments now reports exactly what the shared service says (== what Debt shows), not loan_math.emi
    assert row["amount"] == round(loan_obligation.monthly_emi(loan, date.today()), 2)
