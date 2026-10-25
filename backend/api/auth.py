"""Authentication endpoints.

Login-only (no sign-up). Session is a JWT in an httpOnly cookie. Password reset
is email-based with single-use, expiring tokens.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from api.deps import get_current_user
from auth.email import send_reset_email, smtp_is_configured
from auth.security import (
    create_session_token,
    hash_password,
    hash_reset_token,
    new_reset_token,
    verify_password,
)
from config import settings
from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Response
from models.user import PasswordResetToken, User
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

router = APIRouter(prefix="/auth")


# ── Schemas ──────────────────────────────────────────────────
class LoginIn(BaseModel):
    identifier: str  # username OR email
    password: str


class ForgotIn(BaseModel):
    email: EmailStr


class ResetIn(BaseModel):
    token: str
    new_password: str


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.jwt_ttl_hours * 3600,
        path="/",
    )


# ── Endpoints ────────────────────────────────────────────────
@router.post("/login")
def login(body: LoginIn, response: Response, db: Session = Depends(get_db)):
    ident = body.identifier.strip().lower()
    user = (
        db.query(User)
        .filter((User.username == ident) | (User.email == ident))
        .first()
    )
    # Same error whether identifier or password is wrong (no user enumeration).
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username/email or password")
    token = create_session_token(user.id, user.email)
    _set_session_cookie(response, token)
    return {"id": user.id, "email": user.email, "username": user.username, "name": user.name}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(settings.cookie_name, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    """Who is signed in, and what they may do.

    `permissions` is the RESOLVED map, not the raw grant rows: the frontend must never re-implement
    the role-plus-grants rule, or the two will disagree and the UI will show controls the server then
    refuses. It is also the reason `role` is published alongside — for labelling a person as an
    admin, never for deciding what to render.

    Everything here is a courtesy to the UI. The server has already decided; hiding a control the
    caller cannot use is politeness, not security.
    """
    from auth.permissions import resolve, user_grants
    from auth.roles import Role

    return {
        "id": user.id,
        "email": user.email,
        "username": user.username,
        "name": user.name,
        "role": user.role,
        # The person this login belongs to. Published so the profile screen stops matching a user to
        # a member by comparing names, which it did with a comment admitting as much.
        "member_id": user.member_id,
        "household_id": user.household_id,
        "permissions": {k: int(v) for k, v in resolve(Role(user.role), user_grants(user)).items()},
    }


@router.post("/forgot-password")
def forgot_password(body: ForgotIn, db: Session = Depends(get_db)):
    import logging
    log = logging.getLogger(__name__)

    # Say the true thing FIRST. With no SMTP host this endpoint used to mint a token, send nothing,
    # log a warning nobody reads, and still answer "a reset link has been sent" — so the screen
    # reported success and you waited for mail that could not arrive. Whether mail is configured is a
    # property of the deployment, not of the account, so stating it leaks nothing about who is
    # registered and the anti-enumeration guarantee below is untouched.
    # No token is minted on this path either: an undeliverable token is only a secret sitting in a
    # log file, and the recovery route on a self-hosted box is shell access, not a mail round-trip.
    if not smtp_is_configured():
        log.warning("password reset requested but SMTP is not usable (host and credentials); "
                    "told the caller so "
                    "and minted no token. Recover with: "
                    "./.venv/bin/python scripts/reset_password.py --user <name>")
        return {"message": "This server has no email set up, so a reset link can’t be sent. "
                           "Whoever runs this Artha can set your password directly on the server."}

    user = db.query(User).filter(User.email == body.email.lower()).first()
    # Always return 200 — never reveal whether an email is registered.
    if user and user.is_active:
        raw, token_hash = new_reset_token()
        db.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=token_hash,
                expires_at=datetime.now(timezone.utc)
                + timedelta(minutes=settings.reset_token_ttl_minutes),
            )
        )
        db.commit()
        reset_url = f"{settings.frontend_url}/reset?token={raw}"
        # The response stays deliberately vague (never reveal whether an email is registered), but the
        # SERVER must not be vague with itself. This used to `except Exception: pass`, so with no SMTP
        # host configured every request answered "a reset link has been sent" while nothing was sent
        # and nothing was logged — the reset simply appeared to do nothing.
        try:
            send_reset_email(user.email, reset_url)
        except Exception as exc:
            # A configured-but-failing host is a different case from no host at all: the caller was
            # already told mail is on its way, so the operator needs the link to finish the job.
            log.error("password reset email to %s failed: %r. Link (valid %s min): %s",
                      user.email, exc, settings.reset_token_ttl_minutes, reset_url)
    return {"message": "If that email is registered, a reset link has been sent."}


@router.post("/reset-password")
def reset_password(body: ResetIn, db: Session = Depends(get_db)):
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    token_hash = hash_reset_token(body.token)
    row = (
        db.query(PasswordResetToken)
        .filter(PasswordResetToken.token_hash == token_hash)
        .first()
    )
    now = datetime.now(timezone.utc)
    expired = row and row.expires_at.replace(tzinfo=timezone.utc) < now
    if row is None or row.used_at is not None or expired:
        raise HTTPException(status_code=400, detail="This reset link is invalid or has expired")

    user = db.query(User).filter(User.id == row.user_id).first()
    if user is None:
        raise HTTPException(status_code=400, detail="This reset link is invalid")

    user.password_hash = hash_password(body.new_password)
    row.used_at = now
    db.commit()
    return {"message": "Password updated. You can now log in."}


@router.post("/change-password")
def change_password(body: ChangePasswordIn,
                    user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """Set a new password from inside the app. No email, no token, no link.

    Email-based reset cannot work here: this deployment has no SMTP host, it is reached over a private
    tailnet rather than a public URL, and a household of two does not need a mail round-trip to change
    its own password. The honest flow is the one every other app has and this one did not.

    The current password is required, and that is the whole security model of this endpoint. A session
    cookie alone is not proof of the owner — a borrowed or forgotten unlocked phone carries one — so
    without that check any open session could silently take the account over. It also means this
    cannot serve a LOCKED-OUT user by design; that case is shell access on the server via
    scripts/reset_password.py, where being able to run the command is itself the authentication.
    """
    if not verify_password(body.current_password, user.password_hash):
        # Deliberately not "wrong password" vs "no such user" — this endpoint already knows who you
        # are, so the only thing to hide is nothing. Plain and useful.
        raise HTTPException(status_code=400, detail="That is not your current password.")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if body.new_password == body.current_password:
        raise HTTPException(status_code=400, detail="That is already your password.")

    user.password_hash = hash_password(body.new_password)
    # Any reset link still outstanding would otherwise survive this change and could be used to set
    # the password again afterwards.
    db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user.id,
        PasswordResetToken.used_at.is_(None),
    ).update({"used_at": datetime.now(timezone.utc)}, synchronize_session=False)
    db.commit()
    return {"message": "Password changed."}
