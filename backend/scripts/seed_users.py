"""Create or update an Artha login user (no public sign-up exists).

You set the credentials beforehand. Usage:

    python scripts/seed_users.py "member@example.com" "Member A"
        → prompts for password (hidden)

    # or non-interactively (e.g. from a secret manager):
    ARTHA_USER_PASSWORD='...' python scripts/seed_users.py "x@y.com" "Name" --from-env

Re-running with an existing email updates that user's name + password.
"""

import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auth.security import hash_password  # noqa: E402
from database import SessionLocal  # noqa: E402
from models.user import User  # noqa: E402


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) < 2:
        print('Usage: python scripts/seed_users.py "email" "Name" [--from-env]')
        sys.exit(1)
    email = args[0].strip().lower()
    name = args[1].strip()

    if "--from-env" in sys.argv:
        password = os.environ.get("ARTHA_USER_PASSWORD", "")
        if not password:
            print("ARTHA_USER_PASSWORD env var is required with --from-env")
            sys.exit(1)
    else:
        password = getpass.getpass(f"Password for {email}: ")
        if getpass.getpass("Confirm password: ") != password:
            print("Passwords do not match.")
            sys.exit(1)

    if len(password) < 8:
        print("Password must be at least 8 characters.")
        sys.exit(1)

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if user:
            user.name = name
            user.password_hash = hash_password(password)
            user.is_active = True
            action = "updated"
        else:
            db.add(User(email=email, name=name, password_hash=hash_password(password)))
            action = "created"
        db.commit()
        print(f"User {email} ({name}) {action}.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
