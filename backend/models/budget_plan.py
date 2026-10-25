from datetime import datetime

from sqlalchemy import JSON, Boolean, Column, DateTime, Float, ForeignKey, Integer, String

from database import Base


class BudgetCategory(Base):
    """A standing zero-based budget line for VARIABLE spend (the partner's Budget Plan).

    Two layers: 'weekly' (day-to-day flow — groceries, eating out, fuel…) and 'monthly'
    (paid once a month — utilities, subscriptions, health buffer…). Sinking funds are a
    separate table; income/fixed/EMIs/savings live in cashflow/loans/recurring.

    `monthly_target` is the planned spend. `match_categories` maps this line to the
    categorizer's transaction categories so ACTUAL spend can be summed against the target.
    """

    __tablename__ = "budget_categories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    household_id = Column(Integer, ForeignKey("households.id"), nullable=False,
                          index=True, server_default="1")
    name = Column(String, nullable=False)            # "Eating out & delivery"
    layer = Column(String, nullable=False, default="monthly")  # weekly | monthly
    monthly_target = Column(Float, nullable=False, default=0.0)
    match_categories = Column(JSON, nullable=True)    # ["Food & Dining", ...] txn categories → actual
    note = Column(String, nullable=True)             # "what it covers"
    holder = Column(String, nullable=True, default="Household")
    active = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
