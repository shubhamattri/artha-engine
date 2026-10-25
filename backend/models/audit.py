"""Append-only audit trail — every change to a transaction is recorded as an immutable event.

This is the substrate for: full history ("why is this number what it is"), undo (revert any change,
not just merges), and learning (the system sees what you correct most). Events are NEVER updated or
deleted — a correction of a correction is just another event. `txn_id` is a plain int (no FK) on
purpose, so an event SURVIVES a hard-deleted transaction (the whole point of an audit log).
"""

from datetime import datetime

from database import Base
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String


class TransactionEvent(Base):
    __tablename__ = "transaction_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    household_id = Column(Integer, ForeignKey("households.id"), nullable=False,
                          index=True, server_default="1")
    txn_id = Column(Integer, nullable=True, index=True)   # no FK → survives a hard delete
    action = Column(String, nullable=False)   # edit | archive | unarchive | delete | merge | categorize | create
    field = Column(String, nullable=True)     # for edits: amount|date|merchant|category|account|direction|txn_type
    old_value = Column(String, nullable=True)
    new_value = Column(String, nullable=True)
    actor = Column(String, nullable=False, default="user")  # user | ai | sync | dedup
    # WHICH person, when there was one. `actor` says what KIND of thing acted; with more than one
    # login on a household, "user" no longer identifies anybody. Nullable because a scheduled sync
    # has no person behind it, and a row that predates this column has no way to acquire one.
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
