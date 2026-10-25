from datetime import datetime

from database import Base
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship


class Loan(Base):
    """A loan stored as INPUTS only. EMI, balance, months-left, interest are all
    computed by services.loan_math from these fields — never hardcoded."""

    __tablename__ = "loans"

    id = Column(Integer, primary_key=True, autoincrement=True)
    household_id = Column(Integer, ForeignKey("households.id"), nullable=False,
                          index=True, server_default="1")
    name = Column(String, nullable=False)
    lender = Column(String, nullable=False)
    holder = Column(String, nullable=False)          # a Member key, or "Household"
    category = Column(String, nullable=False)         # Personal | Car | Home | Education | CC ...
    original_principal = Column(Float, nullable=False)
    annual_rate_pct = Column(Float, nullable=False)   # e.g. 10.9
    tenure_months = Column(Integer, nullable=False)
    disbursement_date = Column(Date, nullable=False)
    extra_principal = Column(Float, nullable=False, default=0.0)  # lump prepayments
    rate_type = Column(String, nullable=False, default="fixed")   # fixed | floating (tenure flexes)
    loan_type = Column(String, nullable=False, default="standard")   # standard | flexi
    # Flexi/education-moratorium config lives together here (NULL for standard loans):
    #   {disbursed_amount, disbursements:[{amount,date,note}], moratorium_months, interest_mode}
    flexi_terms = Column(JSON, nullable=True)
    emi_override = Column(Float, nullable=True)        # use if a loan's EMI isn't the formula value
    closed = Column(Boolean, nullable=False, default=False)
    closed_on = Column(Date, nullable=True)
    # EXTERNAL loan (e.g. a parent's gold loan against family gold): it pledges collateral so it belongs
    # in the gold-collateral view, but its EMI is NOT the household's cash-flow — so it's kept OUT of FOIR,
    # the monthly-EMI total, net-worth liabilities, and the payoff strategies. Still shown, tagged external.
    foir_exempt = Column(Boolean, nullable=False, default=False)
    # SERVICED loans are ones the household actually pays each month → their EMI hits cash-flow leftover
    # and their balance is a net-worth liability. Decoupled from foir_exempt on purpose: a gold loan in a
    # parent's name is OFF the FOIR eligibility ratio (foir_exempt=True) yet the household services it, so
    # serviced_by_household=True keeps the ₹EMI in leftover and the balance on the balance sheet. A truly
    # EXTERNAL loan (a parent pays it) is foir_exempt=True AND serviced_by_household=False → fully off books.
    serviced_by_household = Column(Boolean, nullable=False, default=True, server_default="1")
    # GOLD loans (loan_type='gold') are interest-only bullet accounts with a flexible payment ledger, not
    # amortising EMIs. expected_monthly_payment is the nominal ₹ used for the cash-flow leftover projection
    # (actual payments live in loan_payments); the balance/interest come from services.loan_math.compute_gold_state.
    expected_monthly_payment = Column(Float, nullable=True)
    # Whether the lender accepts money EARLY, which decides where spare cash can actually go.
    #   part           any extra reduces principal (floating home loans, gold, most flexi)
    #   foreclose_only nothing until you can clear the whole balance (typical car/personal loans)
    #   none           locked in; even foreclosure is refused for now
    # A payoff plan that ignores this sends cash at a loan that will refuse it, so the dates it
    # promises cannot happen. Defaults to 'part' so existing loans behave exactly as before.
    # WHO the borrowing is for, which is not the same as who pays it. A loan in your name for a
    # sibling is your obligation to SERVICE and their principal to clear: every rupee of prepayment
    # is your wealth moving to them. serviced_by_household answers "does the money leave my account"
    # and foir_exempt answers "does it count against my eligibility"; neither answers "is
    # accelerating this a good use of MY money". 'external' keeps the EMI in cash-flow and the
    # balance on the balance sheet, and takes the loan OUT of the payoff attack order.
    beneficiary = Column(String, nullable=False, default="household",
                         server_default="household")   # household | member | external

    # NULL means "not overridden" — services.prepayment derives it from the rate type, because for
    # a floating-rate individual borrower the RBI decides this, not the lender and not the user.
    prepayment = Column(String, nullable=True)              # part | foreclose_only | none
    prepayment_charge_pct = Column(Float, nullable=True)    # % of outstanding, when one applies
    payments = relationship("LoanPayment", back_populates="loan",
                            cascade="all, delete-orphan", lazy="selectin")
    notes = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class CashflowLine(Base):
    """A recurring monthly cash-flow line — income, fixed expense, or saving.
    Drives the two-ledger engine (salary − EMIs − fixed − savings = leftover).
    EMIs are derived from loans, so they are NOT stored here."""

    __tablename__ = "cashflow_lines"

    id = Column(Integer, primary_key=True, autoincrement=True)
    household_id = Column(Integer, ForeignKey("households.id"), nullable=False,
                          index=True, server_default="1")
    kind = Column(String, nullable=False)             # income | fixed | saving
    label = Column(String, nullable=False)            # "Rent", "Support to brother", "Groww SIP"
    amount_inr = Column(Float, nullable=False)        # monthly amount
    holder = Column(String, nullable=True)            # a member key, or "Household"
    active = Column(Boolean, nullable=False, default=True)
    start_date = Column(Date, nullable=True)          # when this line began (None = always-on / unknown)
    end_date = Column(Date, nullable=True)            # when it stops (None = ongoing)
    created_at = Column(DateTime, default=datetime.utcnow)


class Budget(Base):
    __tablename__ = "budgets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    household_id = Column(Integer, ForeignKey("households.id"), nullable=False,
                          index=True, server_default="1")
    category = Column(String, nullable=False)
    monthly_limit_inr = Column(Float, nullable=False)
    month = Column(String(7), nullable=False)  # yyyy-mm
    # Where this figure came from. NULL = a person typed it, which is what every row was before this
    # existed. "backfill" = a bulk fill over months that had no budget. "default" = seeded from the
    # category's own default target. Without it a decision and an assumption are indistinguishable,
    # and a bulk fill cannot be undone without also deleting what someone set by hand.
    source = Column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint("category", "month", name="uq_budget"),
    )
