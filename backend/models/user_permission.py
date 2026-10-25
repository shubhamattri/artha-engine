"""One grant: this user holds this level of this permission key.

Only members need rows here. An elevated role is `edit` everywhere and the resolver ignores grants
entirely, so an admin's rows are dead weight rather than a second source of truth to keep in step.

Absence means `none`, deliberately. The alternative — writing a row per key per user so every
combination is explicit — means a key added to the catalogue leaves every existing person with no
row for it, and then the code has to decide what a missing row means anyway. Better to decide once,
here, and have it mean the safe thing.
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, SmallInteger, String, UniqueConstraint

from database import Base


class UserPermission(Base):
    __tablename__ = "user_permissions"
    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_user_permission_key"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    # A key from auth.permissions.KEYS. Stored as text rather than an enum so that removing a key
    # from the catalogue cannot make existing rows unreadable — the resolver ignores unknown keys.
    key = Column(String(40), nullable=False)
    # auth.roles.Level as an int: 0 none, 1 view, 2 edit. Ordered so `granted >= required` is the
    # whole comparison.
    level = Column(SmallInteger, nullable=False, default=0)
    granted_at = Column(DateTime, default=datetime.utcnow)
    # Who granted it. Null for rows created by a migration or a script rather than by a person.
    granted_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
