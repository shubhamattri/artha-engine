"""Advisor chat persistence — append-only, multi-tenant storage of every advisor turn.

Every conversation is one AdvisorChat row; every message (user or assistant) is one
AdvisorMessage row. The cited_numbers + tools_called columns are the provenance substrate
that the number-verifier (T7) and the weekly coach (T9) read to catch hallucinated ₹
figures and propose new eval cases. Feedback (👍/👎) is a one-time annotation on the
assistant message — not a content update.

WHY append-only: financial conversations are audit material. We never update or delete
content rows — a user correction is a new message, not a rewrite. The only mutable
fields are the feedback columns (nullable → 'up'/'down') because feedback is annotation,
not retraction.

Multi-tenancy: advisor_chat.household_id mirrors the pattern on transactions.household_id
(ForeignKey + index + server_default='1'). Every query in advisor_store.py filters by
household_id so a household can never read another's conversation.
"""

from __future__ import annotations

from datetime import datetime

from database import Base
from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship


class AdvisorChat(Base):
    """One conversation session between a household and the advisor.

    A new chat is created on every 'New chat' action (or the first message in a fresh
    session). The `lens` captures the PeriodToggle state at chat start so every message
    in that chat knows whether 'this period' meant a billing cycle or a calendar month.
    Archiving does not delete — it marks status='archived' so the coach can still read it.
    """

    __tablename__ = "advisor_chat"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Tenant key — same pattern as transactions.household_id.  server_default=1 = the
    # single live household today; signup will stamp it per-insert once multi-household
    # goes live (BUILD-LOG Slice 3/4).
    household_id = Column(
        Integer, ForeignKey("households.id"), nullable=False,
        index=True, server_default="1"
    )
    # WHOSE conversation. The household shares its money; it does not share its questions. Someone
    # asking whether they can afford something, or about a debt they are worried about, is writing
    # to the advisor, not to the household. So the tenant key still scopes the data and this scopes
    # the reading of it. Nullable for chats that predate logins being distinguishable, which stay
    # visible to the household as they always were.
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    # 'cycle' | 'month' — the global PeriodToggle lens at chat start.  Every tool call
    # in this chat inherits this lens so period-sensitive numbers always use the right
    # window without the model having to track it.
    lens = Column(String(8), nullable=False, default="cycle")
    # 'active' | 'archived' — UI 'New chat' archives the current chat, creating a fresh
    # one.  The coach reads archived chats; nothing is deleted.
    status = Column(String(12), nullable=False, default="active")
    # Optional title: first-user-message summary.  NOT PII-sanitised at write time; the
    # coach gather.py redacts it before including it in any training artifacts.
    title = Column(String, nullable=True)

    messages = relationship(
        "AdvisorMessage",
        back_populates="chat",
        cascade="all, delete-orphan",
        order_by="AdvisorMessage.seq",
    )


class AdvisorMessage(Base):
    """One turn in an advisor conversation — user question OR assistant answer (one row each).

    A complete Q&A exchange is always TWO rows: user (seq=N, role='user') then assistant
    (seq=N+1, role='assistant').  The assistant row carries full provenance:

      cited_numbers — the canonical ₹ figures the answer used (verified by T7)
      tools_called  — which deterministic tools were called and whether they succeeded
      feedback      — the 👍/👎 signal the weekly coach (T9) trains on

    content stores the final prose answer only.  Tool scratch (intermediate tool_use
    blocks) is NOT stored here — it lives only in the run_tools() loop and is discarded
    after the turn completes.  This keeps the table lean and avoids storing Anthropic
    internal API shapes that can change.
    """

    __tablename__ = "advisor_message"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(
        Integer, ForeignKey("advisor_chat.id"), nullable=False, index=True
    )
    # 0-based ordinal within the chat (user=0, assistant=1, user=2, …).  Enforced unique
    # per-chat by the constraint below so the sequence is never ambiguous or gapped.
    seq = Column(Integer, nullable=False)
    # 'user' | 'assistant'
    role = Column(String(9), nullable=False)
    # Raw message text.  For assistant rows: the final prose answer, NOT tool scratch.
    # Using Text (unlimited) because Opus responses can exceed 2 000 tokens of prose.
    content = Column(Text, nullable=False)

    # ── Assistant-only provenance columns (NULL on user rows) ───────────────────────
    # list[ {label:str, value:float, unit:'INR'|'pct'|'months'|'count',
    #         tool:str, source:'tool'|'ground', verified:bool|null} ]
    # T7 re-computes every value here from the DB and fails the eval turn on any mismatch.
    cited_numbers = Column(JSON, nullable=True)
    # list[ {name:str, input:dict, ok:bool} ] in call order — provenance for T7 + T9.
    # household_id is STRIPPED from input before storage so the tenant key is never
    # persisted in JSON (coach gather reads these without risk of cross-tenant leaks).
    tools_called = Column(JSON, nullable=True)
    # e.g. 'claude-opus-4-8' — the model that produced this assistant message; null on
    # user rows and on any assistant row produced before this column existed.
    model = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    # ── Feedback (assistant rows only; NULL = no feedback yet) ──────────────────────
    # 'up' | 'down' — the training signal the weekly coach reads to propose new eval
    # cases (T9).  Never auto-applied; a human merges any proposed changes.
    feedback_rating = Column(String(4), nullable=True)
    # Optional free-text note from the user explaining their rating.
    feedback_note = Column(String, nullable=True)
    # When feedback was submitted — useful for recency weighting in the coach.
    feedback_at = Column(DateTime, nullable=True)

    __table_args__ = (
        # Guarantees seq is unique within a chat so the ordinal is never ambiguous.
        UniqueConstraint("chat_id", "seq", name="uq_advisor_message_seq"),
    )

    chat = relationship("AdvisorChat", back_populates="messages")
