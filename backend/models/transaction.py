from datetime import datetime

from database import Base
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship


class Statement(Base):
    __tablename__ = "statements"

    id = Column(Integer, primary_key=True, autoincrement=True)
    household_id = Column(Integer, ForeignKey("households.id"), nullable=False, index=True)
    filename = Column(String, nullable=False)
    bank = Column(String, nullable=False)
    card_type = Column(String, nullable=False)
    card_last4 = Column(String(4), nullable=False)
    stmt_month = Column(String(7), nullable=False)  # yyyy-mm
    parsed_at = Column(DateTime, default=datetime.utcnow)
    total_due = Column(Float, nullable=True)
    txn_count = Column(Integer, default=0)

    __table_args__ = (
        # filename included: a bank sends more than one file per month (a mid-cycle export
        # and the billed statement), and sharing one record made their rows
        # indistinguishable by origin, which made the double-count uncleanable.
        UniqueConstraint("bank", "card_type", "stmt_month", "filename",
                         name="uq_statement"),
    )

    transactions = relationship(
        "Transaction",
        back_populates="statement",
        cascade="all, delete-orphan",
    )


class DeletedFingerprint(Base):
    """Tombstone — a hard-deleted transaction's fingerprint, so the sync never re-creates it."""

    __tablename__ = "deleted_fingerprints"

    # Composite key: the fingerprint alone stops being unique the moment two households see the same
    # transaction shape, and household B must be able to tombstone what household A already did.
    household_id = Column(Integer, ForeignKey("households.id"), primary_key=True)
    fingerprint = Column(String(40), primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class EvaluatedSms(Base):
    """A bank SMS the LLM has ALREADY judged non-spend (OTP / promo / credit / balance / future-EMI
    reminder). Recorded so the 10-minute sync stops re-sending the same rolling 30-day backlog of
    non-spends to the model on EVERY run — that was ~33 wasted LLM calls per cycle, forever, and kept
    the sync log's `new` count permanently inflated. A real spend the LLM mis-skips is still caught by
    the monthly statement (a different source + fingerprint), so suppressing the SMS re-eval is safe."""

    __tablename__ = "evaluated_sms"

    # Composite key, same reason as deleted_fingerprints: one household having judged an SMS
    # non-spend must not cause another household's identical SMS to be skipped unread.
    household_id = Column(Integer, ForeignKey("households.id"), primary_key=True)
    fingerprint = Column(String(40), primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Tenant key — every row belongs to exactly one household; the read path filters on it so a
    # household can never see another's ledger. server_default=1 = the single real household today,
    # until signup stamps it per-insert (BUILD-LOG Slice 3/4).
    household_id = Column(Integer, ForeignKey("households.id"), nullable=False,
                          index=True, server_default="1")
    statement_id = Column(Integer, ForeignKey("statements.id"), nullable=True)
    fingerprint = Column(String(40), unique=True, nullable=False)  # SHA1
    date = Column(String(10), nullable=False)  # yyyy-mm-dd
    # The PRECISE transaction time when the channel provides it: SMS receipt time (chat.db), email
    # Date header. NULL for statement rows (banks print only a date). Drives Tier-1 timestamp dedup
    # (RECONCILIATION-SPEC.md) — two rows within 15 min of the same amount are certainly one txn.
    occurred_at = Column(DateTime, nullable=True)
    # The bank's own transaction reference (UPI RRN, NEFT/IMPS UTR, "Ref 400123456789") when the
    # message carries one — the globally-unique id for a real debit. Same ref anywhere = the same debit
    # (merge, ignore date); different ref = different debits (never merge). NULL for card POS (no ref).
    bank_ref = Column(String(32), nullable=True, index=True)
    description = Column(String, nullable=False)
    merchant = Column(String, nullable=True)  # clean display name (AI categorization); NULL = not processed
    source = Column(String, nullable=False, default="statement")  # statement | manual | gmail
    # The alert text this row was parsed FROM. Kept because parsing is lossy and its misses are only
    # discoverable later: an account written as "Acc XX456" was mis-read for months, and with the
    # source discarded there was nothing left to re-read. Storing it makes every extraction bug
    # retroactively fixable instead of needing the original SMS or email to still exist.
    source_text = Column(String, nullable=True)
    amount = Column(Float, nullable=False)  # always positive
    direction = Column(String(6), nullable=False)  # debit | credit
    category = Column(String, nullable=True)
    confidence = Column(String(4), nullable=True)  # high | low
    txn_type = Column(String(10), nullable=True)  # SPEND|FEE|EMI|PAYMENT|REFUND
    bank = Column(String, nullable=False)
    card_type = Column(String, nullable=False)
    card_last4 = Column(String(4), nullable=False)
    stmt_month = Column(String(7), nullable=False)
    # NULL = active (counts everywhere). Non-NULL = a duplicate of transaction <dup_of_id>,
    # excluded from every total. Reversible — reconciliation never hard-deletes.
    dup_of_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)
    # soft delete — row kept for history/audit but excluded from every total and list.
    archived = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    statement = relationship("Statement", back_populates="transactions")
