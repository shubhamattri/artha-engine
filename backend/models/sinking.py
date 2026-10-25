from datetime import datetime

from sqlalchemy import JSON, Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String

from database import Base


class SinkingFund(Base):
    """A sinking fund — the partner's core idea: set aside ₹X every month for a lumpy
    future cost (home tickets, gifts, car insurance, festivals…). The balance
    accrues monthly and is DRAWN DOWN when you actually spend (log a withdrawal).

    Balance = opening_balance + monthly_contribution × months_since(as_of) − Σ withdrawals.
    No interest by default (it sits in a savings account); rate optional.
    """

    __tablename__ = "sinking_funds"

    id = Column(Integer, primary_key=True, autoincrement=True)
    household_id = Column(Integer, ForeignKey("households.id"), nullable=False,
                          index=True, server_default="1")
    name = Column(String, nullable=False)              # "Home tickets", "Car insurance"…
    monthly_contribution = Column(Float, nullable=False, default=0.0)
    opening_balance = Column(Float, nullable=False, default=0.0)   # already saved at as_of
    as_of_date = Column(Date, nullable=False)
    target_annual = Column(Float, nullable=True)       # optional: what it should cover per year
    holder = Column(String, nullable=True)             # a member key, or "Household"
    withdrawals = Column(JSON, nullable=True)          # [{amount, date, note}] — money spent from the fund
    active = Column(Boolean, nullable=False, default=True)
    notes = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
