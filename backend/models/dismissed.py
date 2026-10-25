"""A user's decision that two transactions are NOT the same debit.

The duplicate review queue surfaces ambiguous same-amount/same-day pairs that carry no shared bank
reference (so the engine can't decide deterministically). When the user says "these are two real
transactions, not a duplicate", we remember that here so the pair never resurfaces — keyed by
fingerprint (stable across re-imports), not row id.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint

from database import Base


class DismissedDuplicate(Base):
    __tablename__ = "dismissed_duplicate"

    id = Column(Integer, primary_key=True, autoincrement=True)
    household_id = Column(Integer, ForeignKey("households.id"), nullable=False,
                          index=True, server_default="1")
    # the two transaction fingerprints, stored sorted (fp_lo <= fp_hi) so a pair has ONE canonical row
    fp_lo = Column(String(40), nullable=False)
    fp_hi = Column(String(40), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("household_id", "fp_lo", "fp_hi", name="uq_dismissed_pair"),
    )
