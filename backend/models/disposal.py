"""Selling something. The system had no way to say this, so the only options were to flip an asset
to inactive (losing the price, the date and the gain) or to hand-create a fake cash asset for the
proceeds. Both happened: "Maple Heights sale proceeds (receivable)" is a manual cash row standing
in for a sale the model could not express.

What a disposal has to record, and why each field is separate rather than one net number:

  gross_proceeds  what the buyer paid. This, NOT what you pocketed, is the sale consideration for
                  capital gains.
  costs           brokerage, stamp duty, legal. Deductible against the gain.
  loan_settled    proceeds that went straight to closing a loan secured on the asset. This is the
                  field that catches people out: it reduces what reaches your bank by a lot, and
                  reduces the taxable gain by NOTHING. Maple Heights sold for Rs 50,00,000 and
                  left Rs 12,00,000 in hand, but the gain is computed on Rs 50,00,000.
  receivable      proceeds not yet received (registration pending). Real, owed, not yet spendable.
  cost_basis      what it originally cost. Gain = gross_proceeds - costs - cost_basis.
  acquired_on     with disposed_on, decides long-term vs short-term, which sets the tax rate.

Keeping loan_settled apart from costs is the whole point: conflating them understates tax owed.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, Date, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from database import Base


class AssetDisposal(Base):
    __tablename__ = "asset_disposals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    household_id = Column(Integer, ForeignKey("households.id"), nullable=False, index=True)
    asset_id = Column(Integer, ForeignKey("assets.id"), nullable=False, index=True)

    disposed_on = Column(Date, nullable=False)
    acquired_on = Column(Date, nullable=True)        # null → holding period unknown, tax left unrated

    # Null quantity means the whole asset went. A number means a part sale, and the asset stays
    # active with the remainder — selling 40 of 100 shares is not the same as closing the position.
    quantity = Column(Float, nullable=True)

    gross_proceeds_inr = Column(Float, nullable=False)
    costs_inr = Column(Float, nullable=False, default=0.0)          # deductible against the gain
    loan_settled_inr = Column(Float, nullable=False, default=0.0)   # NOT deductible against the gain
    receivable_inr = Column(Float, nullable=False, default=0.0)     # owed but not yet received
    cost_basis_inr = Column(Float, nullable=True)                   # null → gain cannot be computed

    notes = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    receipts = relationship("DisposalReceipt", back_populates="disposal",
                            cascade="all, delete-orphan",
                            order_by="DisposalReceipt.received_on")

    @property
    def received_against_receivable_inr(self) -> float:
        """What has landed so far against this sale's receivable, from the receipt ledger."""
        return round(sum(r.amount_inr for r in self.receipts), 2)

    @property
    def net_received_inr(self) -> float:
        """What actually reached the household so far. Not a tax number."""
        return round(self.gross_proceeds_inr - (self.costs_inr or 0.0)
                     - (self.loan_settled_inr or 0.0) - (self.receivable_inr or 0.0), 2)

    @property
    def realised_gain_inr(self) -> float | None:
        """Capital gain. Deliberately ignores loan_settled — repaying a loan out of the proceeds is
        not a cost of transfer and does not reduce the gain by a rupee."""
        if self.cost_basis_inr is None:
            return None
        return round(self.gross_proceeds_inr - (self.costs_inr or 0.0) - self.cost_basis_inr, 2)


class DisposalReceipt(Base):
    """One instalment of a receivable actually arriving.

    `receivable_inr` on its own is a balance with no history: it went down, and nothing says by how
    much, when, or whether the money reached the balance sheet at all. That is how Rs 4,00,000 was
    moved by hand on both sides with no record, and how an earlier Rs 6,50,000 stayed invisible.
    Every rupee that leaves the receivable now leaves a row here and lands on a cash asset in the
    same transaction.
    """

    __tablename__ = "disposal_receipts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    household_id = Column(Integer, ForeignKey("households.id"), nullable=False, index=True)
    disposal_id = Column(Integer, ForeignKey("asset_disposals.id"), nullable=False, index=True)

    received_on = Column(Date, nullable=False)
    amount_inr = Column(Float, nullable=False)
    # The cash row this credited. Kept so the pair can be traced years later, and nullable because a
    # household may delete that asset without the ledger of what arrived becoming a lie.
    asset_id = Column(Integer, ForeignKey("assets.id"), nullable=True)

    notes = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    disposal = relationship("AssetDisposal", back_populates="receipts")
