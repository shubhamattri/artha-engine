from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String

from database import Base


class CategoryRule(Base):
    """Learned merchant→category memory. signature is the normalized merchant key; `kind` ranks
    trust (user > ai). The classifier reads this before spending an AI call."""

    __tablename__ = "category_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    household_id = Column(Integer, ForeignKey("households.id"), nullable=False,
                          index=True, server_default="1")
    signature = Column(String, unique=True, index=True, nullable=False)
    category = Column(String, nullable=False)
    merchant = Column(String, nullable=True)
    kind = Column(String, nullable=False, default="ai")  # user | ai | rule
    hits = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
