from datetime import datetime

from sqlalchemy import Column, Date, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from database import Base


class LoanPayment(Base):
    """One payment against a (gold) loan — the flexible ledger. A gold loan has no fixed EMI: the
    household pays any amount, any date, several times a month, or skips entirely. Each row here is a real
    payment; services.loan_math.compute_gold_state replays them (interest first, then principal) to derive
    the live balance. Scoped per household; deleting the loan deletes its payments (cascade)."""

    __tablename__ = "loan_payments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    loan_id = Column(Integer, ForeignKey("loans.id"), nullable=False, index=True)
    household_id = Column(Integer, ForeignKey("households.id"), nullable=False,
                          index=True, server_default="1")
    date = Column(Date, nullable=False)
    amount = Column(Float, nullable=False)
    # An 'extra' row is money paid ON TOP of the EMI. A 'skipped_emi' row is the opposite: the
    # scheduled EMI did NOT happen, so the balance is higher than the amortisation assumes. Both live
    # in one ledger because both are the same question — what really moved, versus what was planned.
    kind = Column(String, nullable=False, default="extra", server_default="extra")  # extra | skipped_emi
    note = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    loan = relationship("Loan", back_populates="payments")
