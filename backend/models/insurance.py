from datetime import datetime
from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String
from database import Base


class InsurancePolicy(Base):
    """A life/health insurance policy. Primarily PROTECTION (sum assured), not wealth —
    except Return-of-Premium / endowment / ULIP, whose surrender/maturity value is real money.
    """
    __tablename__ = "insurance_policies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    household_id = Column(Integer, ForeignKey("households.id"), nullable=False,
                          index=True, server_default="1")
    provider = Column(String, nullable=False)            # Max Life, ...
    plan_name = Column(String, nullable=True)
    policy_no = Column(String, nullable=True)
    kind = Column(String, nullable=False, default="term")  # term | trop | endowment | ulip | health
    holder = Column(String, nullable=True)               # policyholder
    life_insured = Column(String, nullable=True)
    sum_assured = Column(Float, nullable=False, default=0.0)
    premium = Column(Float, nullable=False, default=0.0)
    premium_frequency = Column(String, nullable=False, default="monthly")  # monthly | annual
    start_date = Column(Date, nullable=True)
    policy_term_years = Column(Integer, nullable=True)
    premium_paying_term_years = Column(Integer, nullable=True)
    return_of_premium = Column(Boolean, nullable=False, default=False)
    maturity_date = Column(Date, nullable=True)
    surrender_value = Column(Float, nullable=True)        # current cash value (0 for pure term)
    active = Column(Boolean, nullable=False, default=True)
    notes = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
