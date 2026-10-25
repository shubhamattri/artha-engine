"""Invite a user: create the account with a random (unusable) password and send
a secure 'set your password' link by email. No plaintext password is ever set
by anyone — the user chooses it via the emailed token.

Usage:
    python scripts/invite_user.py <email> <username> "<Display Name>"

If SMTP isn't configured, the set-password link is written to
/tmp/artha_reset_links.log (and printed) so onboarding still works locally.
"""

import secrets
import sys
from datetime import datetime, timedelta, timezone
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auth.email import send_reset_email  # noqa: E402
from auth.security import hash_password, new_reset_token  # noqa: E402
from config import settings  # noqa: E402
from database import SessionLocal  # noqa: E402
from models.user import PasswordResetToken, User  # noqa: E402


def main() -> None:
    if len(sys.argv) < 4:
        print('Usage: python scripts/invite_user.py <email> <username> "<Display Name>"')
        sys.exit(1)
    email = sys.argv[1].strip().lower()
    username = sys.argv[2].strip().lower()
    name = sys.argv[3].strip()

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if user is None:
            user = User(
                email=email,
                username=username,
                name=name,
                password_hash=hash_password(secrets.token_urlsafe(32)),  # random, unusable
            )
            db.add(user)
        else:
            user.username = username
            user.name = name
        db.flush()

        raw, token_hash = new_reset_token()
        db.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=token_hash,
                expires_at=datetime.now(timezone.utc)
                + timedelta(hours=settings.invite_token_ttl_hours),
            )
        )
        db.commit()

        link = f"{settings.frontend_url}/reset?token={raw}"
        send_reset_email(email, link)
        sent = "emailed" if settings.smtp_host else "dev-logged (no SMTP configured)"
        print(f"Invited {name} <{email}> as '{username}'. Set-password link {sent}:")
        print(f"  {link}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
