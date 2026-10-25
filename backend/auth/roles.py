"""The role and permission-level vocabulary. One file, no duplicates.

This is deliberately the only place either enum is written down. The frontend receives the values
from the API rather than repeating them, because hand-maintained copies across packages drift, and
the result is a role the backend will serve that the admin UI cannot assign.
"""

from enum import IntEnum, StrEnum


class Level(IntEnum):
    """How much of one permission key a person holds.

    Ordered on purpose: `granted >= required` is the entire comparison the guard makes, so `edit`
    implying `view` falls out of the arithmetic rather than needing a rule.
    """

    none = 0
    view = 1
    edit = 2


class Role(StrEnum):
    member = "member"
    admin = "admin"
    super_admin = "super_admin"

    @property
    def is_elevated(self) -> bool:
        """May edit household data without a per-key grant.

        Written as an explicit membership test at every call site rather than hidden behind an
        `is_admin` flag. A flag like that tends to start as one role and quietly grow, until every
        guard site grants roles nobody meant it to, because nobody re-reads the definition after
        the first change.
        """
        return self in (Role.admin, Role.super_admin)

    @property
    def manages_users(self) -> bool:
        """May create users, change roles, and grant or revoke permissions.

        Exactly one person per household holds this, enforced by a partial unique index rather than
        by application code, so a race or a script cannot produce a second.
        """
        return self is Role.super_admin
