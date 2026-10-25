"""Audit logging — one tiny append-only helper that every mutation calls.

Keep it dumb: record what changed, who changed it, when. The caller owns the commit (so an edit and
its audit row land in the same transaction). Values are stringified so one table holds every field.
"""

from __future__ import annotations

from auth.acting_user import acting_user_id
from models.audit import TransactionEvent
from sqlalchemy.orm import Session


def _s(v) -> str | None:
    return None if v is None else str(v)


def log_event(db: Session, *, household_id: int, txn_id: int | None, action: str,
              field: str | None = None, old=None, new=None, actor: str = "user") -> None:
    """Append one immutable event. Never updates/deletes existing rows. Caller commits."""
    # The person comes from the request rather than the signature: the callers include services
    # several layers below any endpoint, and every one of them would otherwise have to grow a
    # user argument it has no other use for. See auth/acting_user.py.
    db.add(TransactionEvent(
        household_id=household_id, txn_id=txn_id, action=action,
        field=field, old_value=_s(old), new_value=_s(new), actor=actor,
        user_id=acting_user_id(),
    ))


def log_edits(db: Session, *, household_id: int, txn_id: int, changes: list[tuple[str, object, object]],
              actor: str = "user") -> None:
    """Convenience: one event per (field, old, new) that actually changed."""
    for field, old, new in changes:
        if old != new:
            log_event(db, household_id=household_id, txn_id=txn_id, action="edit",
                      field=field, old=old, new=new, actor=actor)
