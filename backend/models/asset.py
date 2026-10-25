"""Asset / holding for net-worth. Market holdings (stocks) carry a ticker +
quantity + currency and are valued from a (stale-OK) price feed; everything else
(FD, EPF, cash) uses a manual value. Last fetched price is cached on the row so
a feed failure falls back to the last known value."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, Column, DateTime, Float, ForeignKey, Integer, String

from database import Base


class Asset(Base):
    __tablename__ = "assets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    household_id = Column(Integer, ForeignKey("households.id"), nullable=False,
                          index=True, server_default="1")
    name = Column(String, nullable=False)
    asset_type = Column(String, nullable=False)        # stock | cash | property | gold | vehicle | other
    holder = Column(String, nullable=True)             # a member key, or "Household"
    ticker = Column(String, nullable=True)             # Yahoo ticker, e.g. GLE.PA (stocks)
    quantity = Column(Float, nullable=True)            # units held (stocks)
    currency = Column(String, nullable=False, default="INR")  # price currency
    manual_value_inr = Column(Float, nullable=True)    # for non-market assets
    last_price = Column(Float, nullable=True)          # cached last fetched unit price
    last_price_at = Column(DateTime, nullable=True)
    # type-specific inputs: gold → {purity}; vehicle → {purchase_price, purchase_date}
    meta = Column(JSON, nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    notes = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
