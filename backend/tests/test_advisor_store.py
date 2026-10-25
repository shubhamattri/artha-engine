"""Unit tests for advisor_store — the append-only, multi-tenant advisor chat store.

Coverage targets:
  - record_turn: creates chat + 2 messages; appends to existing chat; seq is correct
  - record_turn: ValueError when chat_id belongs to a different household
  - list_chats: scoped to household; message_count is accurate
  - get_chat: returns full chat + messages; returns None for another household's chat
  - set_feedback: writes rating/note; ValueError on wrong household; ValueError on user row
  - household_id is stripped from tools_called.input before storage

Uses an in-memory SQLite DB (separate fixture from conftest.py since we need additional
tables not in the shared _TABLES list).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base


@pytest.fixture()
def store_db():
    """In-memory SQLite with only the tables advisor_store needs."""
    # Import models to register them on Base.metadata before create_all.
    from models.advisor_chat import AdvisorChat, AdvisorMessage  # noqa: F401
    from models.tenant import Household  # noqa: F401
    from models.user import User  # noqa: F401  (advisor_chat.user_id points at it)
    # Loading ANY user touches user_permissions — User.permissions is a selectin relationship, so
    # the table has to exist even in a fixture that has nothing to do with permissions.
    from models.user_permission import UserPermission  # noqa: F401

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(
        engine,
        tables=[
            Household.__table__,
            User.__table__,
            UserPermission.__table__,
            AdvisorChat.__table__,
            AdvisorMessage.__table__,
        ],
    )
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def two_households(store_db):
    """Two distinct households — used by tenant-isolation tests."""
    from models.tenant import Household

    a = Household(name="Alpha")
    b = Household(name="Beta")
    store_db.add_all([a, b])
    store_db.commit()
    return a, b


# ── record_turn ───────────────────────────────────────────────────────────────────────


def test_record_turn_creates_chat_and_two_messages(store_db, two_households):
    from services.advisor_store import record_turn
    from models.advisor_chat import AdvisorChat, AdvisorMessage

    a, _ = two_households
    result = record_turn(
        store_db, a.id,
        user_msg="Can I buy a ₹3,000 briefcase?",
        assistant_msg="You have ₹5,000 actually free this cycle — yes, that fits.",
        lens="cycle",
        cited_numbers=[{"label": "actually_free", "value": 5000.0, "unit": "INR",
                        "tool": "actually_free", "source": "tool", "verified": None}],
        tools_called=[{"name": "actually_free", "input": {"lens": "cycle"}, "ok": True}],
        model="claude-opus-4-8",
    )

    assert "chat_id" in result
    assert "user_message_id" in result
    assert "assistant_message_id" in result

    chat = store_db.get(AdvisorChat, result["chat_id"])
    assert chat is not None
    assert chat.household_id == a.id
    assert chat.lens == "cycle"
    assert chat.status == "active"

    user_row = store_db.get(AdvisorMessage, result["user_message_id"])
    assert user_row.seq == 0
    assert user_row.role == "user"
    assert "briefcase" in user_row.content

    asst_row = store_db.get(AdvisorMessage, result["assistant_message_id"])
    assert asst_row.seq == 1
    assert asst_row.role == "assistant"
    assert asst_row.model == "claude-opus-4-8"
    assert asst_row.cited_numbers[0]["label"] == "actually_free"


def test_record_turn_appends_to_existing_chat(store_db, two_households):
    from services.advisor_store import record_turn
    from models.advisor_chat import AdvisorMessage

    a, _ = two_households
    r1 = record_turn(
        store_db, a.id, "First question?", "First answer.",
        lens="cycle", cited_numbers=[], tools_called=[], model="claude-opus-4-8",
    )
    r2 = record_turn(
        store_db, a.id, "Follow-up question?", "Follow-up answer.",
        lens="cycle", cited_numbers=[], tools_called=[], model="claude-opus-4-8",
        chat_id=r1["chat_id"],
    )

    # Same chat, messages continue seq from where we left off.
    assert r2["chat_id"] == r1["chat_id"]
    u2 = store_db.get(AdvisorMessage, r2["user_message_id"])
    a2 = store_db.get(AdvisorMessage, r2["assistant_message_id"])
    assert u2.seq == 2   # after the first pair (0, 1)
    assert a2.seq == 3


def test_record_turn_rejects_wrong_household_for_existing_chat(store_db, two_households):
    from services.advisor_store import record_turn

    a, b = two_households
    r = record_turn(
        store_db, a.id, "A's question", "A's answer",
        lens="cycle", cited_numbers=[], tools_called=[], model="claude-opus-4-8",
    )
    # Household B tries to append to household A's chat — must be rejected.
    with pytest.raises(ValueError, match="does not belong to household"):
        record_turn(
            store_db, b.id, "Injected question", "Injected answer",
            lens="cycle", cited_numbers=[], tools_called=[], model="claude-opus-4-8",
            chat_id=r["chat_id"],
        )


def test_record_turn_strips_household_id_from_tools_called(store_db, two_households):
    """household_id must NEVER be persisted in tools_called.input."""
    from services.advisor_store import record_turn
    from models.advisor_chat import AdvisorMessage

    a, _ = two_households
    r = record_turn(
        store_db, a.id, "Q", "A",
        lens="cycle", cited_numbers=[],
        tools_called=[{
            "name": "actually_free",
            "input": {"lens": "cycle", "household_id": a.id},  # should be stripped
            "ok": True,
        }],
        model="claude-opus-4-8",
    )
    asst = store_db.get(AdvisorMessage, r["assistant_message_id"])
    assert "household_id" not in asst.tools_called[0]["input"]
    assert asst.tools_called[0]["input"]["lens"] == "cycle"  # other fields preserved


# ── list_chats ────────────────────────────────────────────────────────────────────────


def test_list_chats_scoped_to_household(store_db, two_households):
    from services.advisor_store import list_chats, record_turn

    a, b = two_households
    # A has 2 chats, B has 1.
    record_turn(store_db, a.id, "Q1", "A1", "cycle", [], [], "claude-opus-4-8")
    record_turn(store_db, a.id, "Q2", "A2", "month", [], [], "claude-opus-4-8")
    record_turn(store_db, b.id, "Q3", "A3", "cycle", [], [], "claude-opus-4-8")

    a_chats = list_chats(store_db, a.id, user_id=None)
    b_chats = list_chats(store_db, b.id, user_id=None)

    assert len(a_chats) == 2
    assert len(b_chats) == 1


def test_list_chats_message_count_is_accurate(store_db, two_households):
    from services.advisor_store import list_chats, record_turn

    a, _ = two_households
    r = record_turn(store_db, a.id, "Q", "A", "cycle", [], [], "claude-opus-4-8")
    # Append a second turn to the same chat.
    record_turn(store_db, a.id, "Q2", "A2", "cycle", [], [], "claude-opus-4-8",
                chat_id=r["chat_id"])

    chats = list_chats(store_db, a.id, user_id=None)
    assert len(chats) == 1
    assert chats[0]["message_count"] == 4   # 2 turns × 2 messages each
    assert chats[0]["lens"] == "cycle"


# ── get_chat ──────────────────────────────────────────────────────────────────────────


def test_get_chat_returns_chat_and_messages_in_order(store_db, two_households):
    from services.advisor_store import get_chat, record_turn

    a, _ = two_households
    r = record_turn(store_db, a.id, "Q1", "A1", "cycle",
                    cited_numbers=[{"label": "pool", "value": 150000.0}],
                    tools_called=[], model="claude-opus-4-8")

    result = get_chat(store_db, a.id, r["chat_id"], user_id=None)

    assert result is not None
    assert result["chat"]["lens"] == "cycle"
    assert len(result["messages"]) == 2
    seqs = [m["seq"] for m in result["messages"]]
    assert seqs == [0, 1]   # always ordered
    asst = next(m for m in result["messages"] if m["role"] == "assistant")
    assert asst["cited_numbers"][0]["label"] == "pool"


def test_get_chat_returns_none_for_another_households_chat(store_db, two_households):
    from services.advisor_store import get_chat, record_turn

    a, b = two_households
    r = record_turn(store_db, a.id, "Q", "A", "cycle", [], [], "claude-opus-4-8")

    # Household B must not see household A's chat.
    assert get_chat(store_db, b.id, r["chat_id"], user_id=None) is None


# ── set_feedback ──────────────────────────────────────────────────────────────────────


def test_set_feedback_writes_rating_and_note(store_db, two_households):
    from services.advisor_store import set_feedback, record_turn
    from models.advisor_chat import AdvisorMessage

    a, _ = two_households
    r = record_turn(store_db, a.id, "Q", "A", "cycle", [], [], "claude-opus-4-8")

    result = set_feedback(store_db, a.id, r["assistant_message_id"], "up",
                          note="Correct, cited actually_free not the pool.")

    assert result["rating"] == "up"
    assert result["message_id"] == r["assistant_message_id"]

    msg = store_db.get(AdvisorMessage, r["assistant_message_id"])
    assert msg.feedback_rating == "up"
    assert "actually_free" in msg.feedback_note
    assert msg.feedback_at is not None


def test_set_feedback_rejects_wrong_household(store_db, two_households):
    from services.advisor_store import set_feedback, record_turn

    a, b = two_households
    r = record_turn(store_db, a.id, "Q", "A", "cycle", [], [], "claude-opus-4-8")

    with pytest.raises(ValueError, match="does not belong to household"):
        set_feedback(store_db, b.id, r["assistant_message_id"], "down")


def test_set_feedback_rejects_user_row(store_db, two_households):
    from services.advisor_store import set_feedback, record_turn

    a, _ = two_households
    r = record_turn(store_db, a.id, "Q", "A", "cycle", [], [], "claude-opus-4-8")

    with pytest.raises(ValueError, match="assistant messages"):
        set_feedback(store_db, a.id, r["user_message_id"], "up")


def test_set_feedback_rejects_invalid_rating(store_db, two_households):
    from services.advisor_store import set_feedback, record_turn

    a, _ = two_households
    r = record_turn(store_db, a.id, "Q", "A", "cycle", [], [], "claude-opus-4-8")

    with pytest.raises(ValueError, match="rating must be"):
        set_feedback(store_db, a.id, r["assistant_message_id"], "meh")


# ── ownership ─────────────────────────────────────────────────────────────────────────
#
# The household shares its money; it does not share its questions. "Can we afford this" and "how
# bad is this loan" are asked of the advisor, not of the household, and the read side has to keep
# that true even though every chat sits in one tenant.


def _person(db, household_id: int, name: str):
    from models.user import User

    u = User(household_id=household_id, email=f"{name}@x.test", username=name, name=name,
             password_hash="x", role="member")
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _turn(db, household_id: int, user_id: int | None, msg: str) -> int:
    """One recorded turn, as the given person. Authorship is ambient, exactly as in a request."""
    from auth.acting_user import set_acting_user
    from services.advisor_store import record_turn

    set_acting_user(user_id)
    try:
        return record_turn(
            db=db, household_id=household_id, user_msg=msg, assistant_msg="ok",
            lens="cycle", cited_numbers=[], tools_called=[], model="test",
        )["chat_id"]
    finally:
        set_acting_user(None)


def test_a_persons_chat_list_is_their_own(store_db, two_households):
    a, _ = two_households
    me, you = _person(store_db, a.id, "me"), _person(store_db, a.id, "you")
    mine = _turn(store_db, a.id, me.id, "mine")
    _turn(store_db, a.id, you.id, "yours")

    from services.advisor_store import list_chats
    assert [c["id"] for c in list_chats(store_db, a.id, user_id=me.id)] == [mine]


def test_one_person_cannot_open_anothers_conversation(store_db, two_households):
    a, _ = two_households
    me, you = _person(store_db, a.id, "me"), _person(store_db, a.id, "you")
    yours = _turn(store_db, a.id, you.id, "yours")

    from services.advisor_store import get_chat
    assert get_chat(store_db, a.id, yours, user_id=me.id) is None
    assert get_chat(store_db, a.id, yours, user_id=you.id) is not None


def test_chats_written_before_authorship_existed_stay_household_wide(store_db, two_households):
    """They were the household's when they were written, and nothing recorded whose they were.
    Hiding them retroactively would lose them; guessing an owner would invent one."""
    a, _ = two_households
    me = _person(store_db, a.id, "me")
    old = _turn(store_db, a.id, None, "from before")

    from services.advisor_store import get_chat, list_chats
    assert old in [c["id"] for c in list_chats(store_db, a.id, user_id=me.id)]
    assert get_chat(store_db, a.id, old, user_id=me.id) is not None


def test_reading_across_the_household_is_deliberate_not_default(store_db, two_households):
    """`user_id` has no default, so the weekly coach has to say out loud that it reads everyone."""
    import inspect

    from services.advisor_store import get_chat, list_chats
    for fn in (list_chats, get_chat):
        p = inspect.signature(fn).parameters["user_id"]
        assert p.default is inspect.Parameter.empty, f"{fn.__name__} would default to a scope"
        assert p.kind is inspect.Parameter.KEYWORD_ONLY


def test_a_chat_records_who_started_it(store_db, two_households):
    from models.advisor_chat import AdvisorChat

    a, _ = two_households
    me = _person(store_db, a.id, "me")
    chat_id = _turn(store_db, a.id, me.id, "hello")
    assert store_db.get(AdvisorChat, chat_id).user_id == me.id
