"""Set a user's role from the command line.

Exists because of one specific trap. Migration 069 makes every existing user a `member` except the
one named in ARTHA_SUPER_ADMIN_USERNAME, and a member with no grants is denied everything — so the
first deploy of the permission layer locks every other person out of a household they co-own. They
would sign in successfully and find an app that refuses them, which reads as the app being broken.

Doing it in the migration was the wrong shape: a migration that hands out access as a side effect is
exactly the accident this layer exists to prevent, and it would run again on any household that
installs Artha later. So it lives here, explicit, run once, by a person who decided.

Usage:
    python scripts/set_role.py <username> <member|admin|super_admin>
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auth.roles import Role  # noqa: E402
from database import SessionLocal  # noqa: E402
from models.user import User  # noqa: E402


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: python scripts/set_role.py <username> <member|admin|super_admin>")
        sys.exit(1)
    username, role_name = sys.argv[1].strip().lower(), sys.argv[2].strip()

    try:
        role = Role(role_name)
    except ValueError:
        print(f"✗ unknown role {role_name!r}. One of: {', '.join(r.value for r in Role)}")
        sys.exit(1)

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if user is None:
            print(f"✗ no user named {username!r}")
            sys.exit(1)

        # The database enforces one super admin per household with a partial unique index, so this
        # would fail as an integrity error. Saying it in a sentence beats a traceback.
        if role is Role.super_admin:
            held = (db.query(User)
                    .filter(User.household_id == user.household_id,
                            User.role == Role.super_admin.value, User.id != user.id)
                    .first())
            if held is not None:
                print(f"✗ {held.username} is already the super admin of this household. "
                      "Demote them first — there is exactly one.")
                sys.exit(1)

        was = user.role
        user.role = role.value
        db.commit()
        print(f"✓ {username}: {was} → {role.value}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
