"""What a cycle's leftover was earmarked for, and what has been drawn against it.

A surplus is NOT money. The rupees are already in a bank account and that balance is already counted
in net worth — recording the surplus as an asset would count the same money twice, which is the
exact shape of the errors that made this app untrustworthy before.

So this table records a CLAIM on money already held: "of the balance I have, this much came from a
cycle I underspent, and it is intended for prepayment / investing / a buffer". Spending it does not
reduce net worth; it moves the same rupees from cash into a loan or an investment, and net worth
only changes by whatever interest the move avoids or earns.

`drawn` is what has already been used. remaining = amount - drawn. A draw links to the thing it paid
for, so "what happened to that money" is answerable, which was the whole point.
"""

from datetime import datetime

from database import Base
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String


class SurplusAllocation(Base):
    """One cycle's leftover, earmarked for a purpose."""

    __tablename__ = "surplus_allocations"

    id = Column(Integer, primary_key=True)
    household_id = Column(Integer, ForeignKey("households.id"), nullable=False, index=True)
    cycle_end = Column(String, nullable=False)        # the cycle this came from, yyyy-mm-dd
    amount = Column(Float, nullable=False)
    purpose = Column(String, nullable=False)          # prepayment | invest | buffer | unallocated
    note = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class SurplusDraw(Base):
    """Money taken out of an allocation, and what it went to."""

    __tablename__ = "surplus_draws"

    id = Column(Integer, primary_key=True)
    # Against the HOUSEHOLD, not an allocation. Requiring an earmark before money could be spent put
    # friction in the wrong place: the money exists whether or not it has been categorised, and a
    # step you must remember is a step that gets skipped.
    household_id = Column(Integer, ForeignKey("households.id"), nullable=True, index=True)
    allocation_id = Column(Integer, ForeignKey("surplus_allocations.id"), nullable=True, index=True)
    funded_from = Column(String, nullable=True)   # wallet | bank | other — recorded, not assumed
    amount = Column(Float, nullable=False)
    # Exactly one of these identifies where it went. A draw with neither is a manual withdrawal and
    # must still be recorded, otherwise the remaining figure quietly overstates what is available.
    loan_id = Column(Integer, ForeignKey("loans.id"), nullable=True)
    recurring_id = Column(Integer, ForeignKey("recurring_investments.id"), nullable=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)
    note = Column(String, nullable=True)
    drawn_on = Column(String, nullable=False)         # yyyy-mm-dd
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
