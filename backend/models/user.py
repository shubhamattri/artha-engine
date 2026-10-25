"""Auth models: User and PasswordResetToken.

Login-only product: users are pre-created (seed_users.py); there is no public
sign-up. Passwords are stored only as bcrypt hashes. Reset tokens are stored
only as SHA-256 hashes, are single-use, and expire.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import relationship

from database import Base


class User(Base):
    __tablename__ = "users"

    # Exactly one super admin per household. Declared HERE as well as in migration 069 so that any
    # `create_all` — tests, a fresh install — gets the rule too. A constraint that lives only in a
    # migration is a constraint the test suite never exercises, which is how you end up trusting a
    # green run that proves nothing about the thing you care about.
    __table_args__ = (
        Index("uq_one_super_admin_per_household", "household_id", unique=True,
              postgresql_where=text("role = 'super_admin'"),
              sqlite_where=text("role = 'super_admin'")),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    # The household this user belongs to — the source of tenancy. get_current_household resolves
    # via this (not "the first household"), so each user only ever sees their own household's data.
    household_id = Column(Integer, ForeignKey("households.id"), nullable=False,
                          index=True, server_default="1")
    email = Column(String, unique=True, nullable=False, index=True)
    username = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)
    password_hash = Column(String, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    # member | admin | super_admin. Defaults to the least powerful: a new family member who arrives
    # as an admin because nobody set a column is the failure the whole permission layer exists to
    # prevent. Exactly one super_admin per household, enforced by a partial unique index (migration
    # 069) rather than by application code, because a Python check loses races and every seeding
    # script writes this table directly.
    role = Column(String(20), nullable=False, server_default="member", default="member")
    # The PERSON this login belongs to. Without it a human is represented twice with nothing joining
    # them: a User row that can sign in, and a Member row that holds cards and a date of birth. The
    # profile screen then has to guess which Member the signed-in user is, and onboarding writes to a
    # record the user cannot see. One person, one profile, reachable from the login.
    member_id = Column(Integer, ForeignKey("members.id"), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # selectin, not lazy: the request path resolves permissions once per request, and a lazy load
    # here would be one extra query on every authenticated call.
    permissions = relationship(
        "UserPermission", cascade="all, delete-orphan", lazy="selectin",
        foreign_keys="UserPermission.user_id",
    )

    reset_tokens = relationship(
        "PasswordResetToken", back_populates="user", cascade="all, delete-orphan"
    )


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    token_hash = Column(String(64), unique=True, nullable=False, index=True)  # sha256 hex
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="reset_tokens")
