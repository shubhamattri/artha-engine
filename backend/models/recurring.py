from datetime import datetime

from sqlalchemy import JSON, Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String

from database import Base


class RecurringInvestment(Base):
    """An accruing/recurring investment whose value is COMPUTED from inputs, not
    fetched live: SIP, RD, EPF, PPF. Net worth = market assets + these.

    The value is a deterministic accrual from an opening balance + periodic
    contributions + a rate, so it never needs a price feed or a cron — it is
    recomputed on every read from `as_of_date` to today.
    """

    __tablename__ = "recurring_investments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    household_id = Column(Integer, ForeignKey("households.id"), nullable=False,
                          index=True, server_default="1")
    name = Column(String, nullable=False)
    kind = Column(String, nullable=False)             # sip | rd | epf | ppf
    holder = Column(String, nullable=True)            # a member key

    # Starting point: the balance already accumulated when we begin tracking.
    opening_balance = Column(Float, nullable=False, default=0.0)
    as_of_date = Column(Date, nullable=False)         # date the opening_balance is true

    # Recurring inflow (per month). employer_contribution is the EPF employer match.
    monthly_contribution = Column(Float, nullable=False, default=0.0)
    employer_contribution = Column(Float, nullable=False, default=0.0)

    annual_rate_pct = Column(Float, nullable=False, default=0.0)   # fixed (RD/EPF/PPF) or assumed (SIP)
    compounding = Column(String, nullable=False, default="annual")  # monthly | quarterly | annual

    maturity_date = Column(Date, nullable=True)        # RD / PPF — when it matures
    contributions = Column(JSON, nullable=True)        # ad-hoc lumpy adds: [{date, amount, note}]
    skips = Column(JSON, nullable=True)                # per-month contribution reductions: [{month:"YYYY-MM", amount}]
    # Market-linked valuation (SIP/MF): {scheme_code, units, last_nav, last_nav_at}.
    # When set, current value = units × live NAV instead of the rate projection.
    market = Column(JSON, nullable=True)

    # True = the recorded `contributions` ledger IS this holding's history, so nothing may be
    # invented on top of it. False/NULL = the ledger holds only ad-hoc entries and the monthly
    # instruction is still accrued forward. See alembic/067.
    ledger_is_authoritative = Column(Boolean, nullable=False, default=False, server_default="0")

    active = Column(Boolean, nullable=False, default=True)  # still contributing?
    # True = debits the bank each month (SIP/RD/PPF) → reduces monthly leftover.
    # False = deducted at source (EPF employee + employer) → never hits take-home.
    from_takehome = Column(Boolean, nullable=False, default=True)
    # What the money is FOR, independent of the instrument it sits in. An RD funding a donation is
    # still an RD, but it never becomes household wealth, so it must not count toward the corpus or
    # skew the debt/equity ratio. It IS a real bank outflow, so it stays in takehome_outflow.
    purpose = Column(String, nullable=False, default="wealth", server_default="wealth")  # wealth | donation
    notes = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
