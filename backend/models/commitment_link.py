from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint

from database import Base


class CommitmentLink(Base):
    """Learned association: a payee / narration signature ↔ a recurring commitment (loan:3, cashflow:8…).

    Seeded automatically from confident matches and from the user confirming one, then used to match
    FUTURE debits by IDENTITY — amount-independent. So a first/last EMI, a floating-rate reset or a
    part-payment still links to the right loan, without any hardcoded lender name or amount threshold.
    The system learns each commitment's real payee from evidence, exactly like the category store does.
    """

    __tablename__ = "commitment_links"
    __table_args__ = (UniqueConstraint("household_id", "signature", name="uq_commitment_link_sig"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    household_id = Column(Integer, ForeignKey("households.id"), nullable=False, index=True)
    signature = Column(String, nullable=False, index=True)   # normalized payee (services.learned.signature)
    commitment_ref = Column(String, nullable=False)          # "loan:3" | "cashflow:8" | "recurring:12" …
    hits = Column(Integer, nullable=False, default=0)
    source = Column(String, nullable=False, default="auto")  # auto (learned from a match) | user (confirmed)
    created_at = Column(DateTime, default=datetime.utcnow)
