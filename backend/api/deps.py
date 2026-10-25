"""Shared API dependencies — current-user auth guard."""

from __future__ import annotations

from fastapi import Cookie, Depends, HTTPException
from sqlalchemy.orm import Session

from auth.security import decode_session_token
from config import settings
from database import get_db
from models.tenant import Household
from models.user import User


def get_current_user(
    db: Session = Depends(get_db),
    session: str | None = Cookie(default=None, alias=settings.cookie_name),
) -> User:
    """Resolve the logged-in user from the session cookie, or 401."""
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_session_token(session)
    if not payload:
        raise HTTPException(status_code=401, detail="Session expired or invalid")
    user = db.query(User).filter(User.id == int(payload["sub"])).first()
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return user


def get_current_household(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Household:
    """The household whose data the request may access — resolved from the logged-in user's
    household_id (NOT "the first household"), so each user only ever sees their own household's
    ledger. Centralised here so every handler resolves tenancy the same way."""
    household = db.query(Household).filter(Household.id == user.household_id).first()
    if household is None:
        raise HTTPException(status_code=500, detail="No household provisioned for this user")
    return household


def get_optional_user(
    db: Session = Depends(get_db),
    session: str | None = Cookie(default=None, alias=settings.cookie_name),
) -> User | None:
    """The signed-in user, or None — never raises.

    Exists for the permission gate, which is mounted on every router INCLUDING the one carrying
    /api/config. That route has to answer before a session exists (the login screen renders the
    wordmark from it), while /api/members on the same router must not. A gate depending on
    `get_current_user` would 401 the first; one that skipped the router would leave the second
    ungated. So the gate takes "user or nothing" and decides for itself, per route, whether a user
    was required.

    A dependency rather than a direct call so `app.dependency_overrides` still works in tests.
    """
    if not session:
        return None
    payload = decode_session_token(session)
    if not payload:
        return None
    user = db.query(User).filter(User.id == int(payload["sub"])).first()
    return user if (user and user.is_active) else None
