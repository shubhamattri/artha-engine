"""Append-only, multi-tenant store for advisor conversations.

This is the ONLY writer for advisor_chat and advisor_message — tenancy, seq ordering,
and the append-only invariant all live here so they can't be violated by accident in
another module.

Public API:
  record_turn  — persist one Q&A exchange (creates chat on first call; appends thereafter)
  list_chats   — recent chats for a household with message counts
  get_chat     — full chat + messages (scoped by household_id)
  set_feedback — annotate an assistant message with 👍/👎

Multi-tenancy hard rule: every function that reads or writes data filters by household_id.
The household_id is NEVER taken from caller-supplied chat/message content — it is always
passed explicitly from the HTTP layer (get_current_household), matching the pattern used
everywhere else in the codebase.
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import or_ as sa_or

# ── Public API ────────────────────────────────────────────────────────────────────────


def record_turn(
    db,
    household_id: int,
    user_msg: str,
    assistant_msg: str,
    lens: str,
    cited_numbers: list[dict],
    tools_called: list[dict],
    model: str,
    chat_id: int | None = None,
) -> dict:
    """Persist one complete Q&A turn (two rows: user + assistant).

    If chat_id is None a new AdvisorChat is created.  Otherwise the messages are
    appended to the existing chat — after verifying it belongs to household_id (raises
    ValueError if not, equivalent to a 404 in the API layer).

    Returns {chat_id, user_message_id, assistant_message_id} so the caller (cockpit.py)
    can hand the assistant_message_id to the UI for feedback attachment (T2).
    """
    # Local imports avoid import-time cycles — same pattern as cashflow/networth/cockpit.
    from models.advisor_chat import AdvisorChat, AdvisorMessage
    from sqlalchemy import func

    if chat_id is None:
        # First turn in a new session — create the conversation header.
        # Authorship comes from the request, like the audit trail's — advisor_agent sits several
        # calls below any endpoint and carries no user. See auth/acting_user.py. None outside a
        # request (a scheduled job) is a truthful answer and the read side treats it as shared.
        from auth.acting_user import acting_user_id

        chat = AdvisorChat(
            household_id=household_id,
            user_id=acting_user_id(),
            lens=lens,
            status="active",
        )
        db.add(chat)
        db.flush()   # populate chat.id before inserting messages
        next_seq = 0
    else:
        # Continuing an existing conversation — verify tenancy.
        chat = (
            db.query(AdvisorChat)
            .filter(
                AdvisorChat.id == chat_id,
                AdvisorChat.household_id == household_id,
            )
            .first()
        )
        if chat is None:
            raise ValueError(
                f"chat {chat_id} not found or does not belong to household {household_id}"
            )
        # Compute the next available seq by reading the current maximum — we never
        # assume a gap-free sequence; we always look at what's actually there.
        current_max = (
            db.query(func.max(AdvisorMessage.seq))
            .filter(AdvisorMessage.chat_id == chat_id)
            .scalar()
        )
        next_seq = 0 if current_max is None else current_max + 1

    # User message — no provenance columns (those are assistant-only).
    user_row = AdvisorMessage(
        chat_id=chat.id,
        seq=next_seq,
        role="user",
        content=user_msg,
    )
    db.add(user_row)
    db.flush()   # populate user_row.id

    # Assistant message — carries full provenance for T7 + T9.
    # household_id is explicitly stripped from tools_called inputs before storage;
    # callers are expected to pass already-sanitised dicts (advisor_agent.py handles this),
    # but we sanitise defensively here as well.
    safe_tools = _strip_household_id(tools_called or [])

    asst_row = AdvisorMessage(
        chat_id=chat.id,
        seq=next_seq + 1,
        role="assistant",
        content=assistant_msg,
        cited_numbers=cited_numbers or [],
        tools_called=safe_tools,
        model=model,
    )
    db.add(asst_row)
    db.commit()

    return {
        "chat_id": chat.id,
        "user_message_id": user_row.id,
        "assistant_message_id": asst_row.id,
    }


def list_chats(db, household_id: int, *, user_id: int | None, limit: int = 50) -> list[dict]:
    """Recent conversations for ONE PERSON in a household, newest first, with message counts.

    `user_id` is required rather than defaulted, because the safe default and the useful default
    point in opposite directions and only the caller knows which it wants. A request handler must
    pass the signed-in user. The weekly coach reads across everyone and passes None deliberately.

    Two filters, doing different jobs: `household_id` is tenancy and is never optional, `user_id`
    is ownership. Chats written before authorship was recorded have a NULL owner and stay visible
    to the household, which is what they were when they were written.
    """
    from models.advisor_chat import AdvisorChat, AdvisorMessage
    from sqlalchemy import func

    rows = (
        db.query(AdvisorChat, func.count(AdvisorMessage.id).label("message_count"))
        .outerjoin(AdvisorMessage, AdvisorMessage.chat_id == AdvisorChat.id)
        .filter(AdvisorChat.household_id == household_id)
        .filter(sa_or(AdvisorChat.user_id == user_id, AdvisorChat.user_id.is_(None))
                if user_id is not None else sa.true())
        .group_by(AdvisorChat.id)
        .order_by(AdvisorChat.created_at.desc())
        .limit(limit)
        .all()
    )

    return [
        {
            "id": chat.id,
            "created_at": chat.created_at,
            "lens": chat.lens,
            "status": chat.status,
            "title": chat.title,
            "message_count": count,
        }
        for chat, count in rows
    ]


def get_chat(db, household_id: int, chat_id: int, *, user_id: int | None) -> dict | None:
    """Full conversation with all messages, scoped by household AND by owner.

    `user_id` required for the same reason as in list_chats. None reads across the household.

    Returns {chat:{id,created_at,lens,status,title}, messages:[...]} or None if
    the chat doesn't exist, belongs to a different household, or belongs to someone else.

    The messages list includes only the fields the UI + evaluators need:
      id, seq, role, content, cited_numbers, tools_called, feedback_rating.
    feedback_note and feedback_at are omitted (not needed by the callers declared
    in the contract; the coach reads them directly via SQL in gather.py).
    """
    from models.advisor_chat import AdvisorChat, AdvisorMessage

    chat = (
        db.query(AdvisorChat)
        .filter(
            AdvisorChat.id == chat_id,
            AdvisorChat.household_id == household_id,
        )
        .filter(sa_or(AdvisorChat.user_id == user_id, AdvisorChat.user_id.is_(None))
                if user_id is not None else sa.true())
        .first()
    )
    if chat is None:
        return None

    messages = (
        db.query(AdvisorMessage)
        .filter(AdvisorMessage.chat_id == chat_id)
        .order_by(AdvisorMessage.seq)
        .all()
    )

    return {
        "chat": {
            "id": chat.id,
            "created_at": chat.created_at,
            "lens": chat.lens,
            "status": chat.status,
            "title": chat.title,
        },
        "messages": [
            {
                "id": m.id,
                "seq": m.seq,
                "role": m.role,
                "content": m.content,
                "cited_numbers": m.cited_numbers,
                "tools_called": m.tools_called,
                "feedback_rating": m.feedback_rating,
            }
            for m in messages
        ],
    }


def set_feedback(
    db,
    household_id: int,
    message_id: int,
    rating: str,
    note: str | None = None,
) -> dict:
    """Annotate an assistant message with 👍/👎 feedback.

    Verifies that the message belongs to a chat owned by household_id before writing
    (raises ValueError on mismatch — the API layer maps this to 404/403).  Only
    assistant messages may carry feedback; calling on a user row raises ValueError.

    This is the only MUTATION this store performs — and even then we're only writing
    to the nullable annotation columns, not altering content.
    """
    from models.advisor_chat import AdvisorChat, AdvisorMessage

    # Join through advisor_chat so the tenancy check is a single atomic query.
    msg = (
        db.query(AdvisorMessage)
        .join(AdvisorChat, AdvisorMessage.chat_id == AdvisorChat.id)
        .filter(
            AdvisorMessage.id == message_id,
            AdvisorChat.household_id == household_id,
        )
        .first()
    )
    if msg is None:
        raise ValueError(
            f"message {message_id} not found or does not belong to household {household_id}"
        )
    if msg.role != "assistant":
        raise ValueError(
            f"feedback can only be set on assistant messages "
            f"(message {message_id} has role={msg.role!r})"
        )
    if rating not in ("up", "down"):
        raise ValueError(f"rating must be 'up' or 'down', got {rating!r}")

    msg.feedback_rating = rating
    msg.feedback_note = note
    msg.feedback_at = datetime.utcnow()
    db.commit()

    return {"message_id": message_id, "rating": rating}


# ── Internal helpers ─────────────────────────────────────────────────────────────────


def _strip_household_id(tools_called: list[dict]) -> list[dict]:
    """Remove household_id from every tool call's input dict before storage.

    household_id is injected server-side into every tool call but must NEVER be
    persisted in the JSON column — the coach reads these rows without auth context
    and cross-tenant leaks via stored JSON would be a silent security hole.
    """
    result = []
    for call in tools_called:
        sanitised = dict(call)
        if isinstance(sanitised.get("input"), dict):
            inp = dict(sanitised["input"])
            inp.pop("household_id", None)
            sanitised["input"] = inp
        result.append(sanitised)
    return result
