"""Managing people: who exists, what role they hold, what they may do.

None of this existed. Users were created by `scripts/seed_users.py` and `scripts/invite_user.py`,
there was no way to list them, and no way to change anything about one — so a permission system with
no way to grant a permission would have been decoration.

Every route here is super-admin only, checked as a ROLE rather than a permission key. Managing people
is deliberately not grantable: a permission that can hand out permissions is a permission that
escalates itself.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.deps import get_current_household, get_current_user
from auth.permissions import KEYS, resolve, user_grants
from auth.roles import Role
from database import get_db
from models.tenant import Household
from models.user import User
from models.user_permission import UserPermission

router = APIRouter()


def require_super_admin(user: User = Depends(get_current_user)) -> User:
    """The one check that is a role and not a key.

    `Role.manages_users` is true for exactly one person per household, enforced by a partial unique
    index. Keeping it out of the twelve keys means it cannot be granted, and therefore cannot be
    granted BY someone who was granted it.
    """
    if not Role(user.role).manages_users:
        raise HTTPException(
            status_code=403,
            detail="Only the household's super admin can manage people and permissions.",
        )
    return user


class RoleIn(BaseModel):
    role: str


class GrantIn(BaseModel):
    key: str
    level: int = Field(ge=0, le=2)


class GrantsIn(BaseModel):
    grants: list[GrantIn]


def _view(u: User) -> dict:
    return {
        "id": u.id, "username": u.username, "email": u.email, "name": u.name,
        "role": u.role, "is_active": u.is_active, "member_id": u.member_id,
        # The resolved map, so the admin screen shows what a person can actually do rather than the
        # rows behind it. An admin's rows are empty and their access is total; showing the rows would
        # read as "no permissions".
        "permissions": {k: int(v) for k, v in resolve(Role(u.role), user_grants(u)).items()},
    }


@router.get("/admin/users")
def list_users(db: Session = Depends(get_db),
               _: User = Depends(require_super_admin),
               household: Household = Depends(get_current_household)):
    """Everyone in THIS household. Scoped like everything else — a super admin administers their own
    household, not the table."""
    users = (db.query(User).filter(User.household_id == household.id)
             .order_by(User.id).all())
    return {"users": [_view(u) for u in users], "keys": list(KEYS)}


@router.patch("/admin/users/{user_id}/role")
def set_role(user_id: int, body: RoleIn, db: Session = Depends(get_db),
             actor: User = Depends(require_super_admin),
             household: Household = Depends(get_current_household)):
    """Change someone's role.

    Two refusals worth stating. A super admin cannot demote themselves, because the household would
    be left with nobody who can grant anything and no way back in. And promoting a second super
    admin is refused here with a readable message rather than left to the unique index, which would
    surface as a 500.
    """
    try:
        role = Role(body.role)
    except ValueError:
        raise HTTPException(status_code=400,
                            detail=f"Unknown role {body.role!r}. Use one of: "
                                   f"{', '.join(r.value for r in Role)}.")

    target = (db.query(User)
              .filter(User.id == user_id, User.household_id == household.id).first())
    if target is None:
        raise HTTPException(status_code=404, detail="No such user in this household")

    if target.id == actor.id and role is not Role.super_admin:
        raise HTTPException(
            status_code=400,
            detail=("You cannot give up super admin here — the household would have nobody who can "
                    "grant permissions. Promote someone else first."),
        )
    if role is Role.super_admin and target.id != actor.id:
        raise HTTPException(
            status_code=400,
            detail=("There is one super admin per household. Make yourself an admin only after "
                    "someone else holds it, which this endpoint deliberately cannot do in one step."),
        )

    target.role = role.value
    db.commit()
    return _view(target)


@router.patch("/admin/users/{user_id}/permissions")
def set_permissions(user_id: int, body: GrantsIn, db: Session = Depends(get_db),
                    actor: User = Depends(require_super_admin),
                    household: Household = Depends(get_current_household)):
    """Replace this user's grants with exactly what was sent.

    Replace rather than merge: a partial update leaves the caller unable to see what a person ends up
    with without re-reading, and "revoke" becomes a different call from "grant". Sending level 0 and
    sending nothing both mean none.
    """
    target = (db.query(User)
              .filter(User.id == user_id, User.household_id == household.id).first())
    if target is None:
        raise HTTPException(status_code=404, detail="No such user in this household")

    unknown = sorted({g.key for g in body.grants} - set(KEYS))
    if unknown:
        raise HTTPException(status_code=400,
                            detail=f"Unknown permission keys: {', '.join(unknown)}")

    db.query(UserPermission).filter(UserPermission.user_id == target.id).delete()
    for g in body.grants:
        if g.level > 0:      # a stored `none` row and no row mean the same thing; keep one shape
            db.add(UserPermission(user_id=target.id, key=g.key, level=g.level,
                                  granted_at=datetime.utcnow(), granted_by_user_id=actor.id))
    db.commit()
    db.refresh(target)
    return _view(target)


@router.post("/admin/users/{user_id}/deactivate")
def deactivate_user(user_id: int, db: Session = Depends(get_db),
                    actor: User = Depends(require_super_admin),
                    household: Household = Depends(get_current_household)):
    """Turn off a login without deleting the person.

    Deactivate rather than delete: the member row carries identity used to unlock statement PDFs, and
    their transactions are part of the household's history. Removing the login removes access, which
    is the thing actually wanted.
    """
    target = (db.query(User)
              .filter(User.id == user_id, User.household_id == household.id).first())
    if target is None:
        raise HTTPException(status_code=404, detail="No such user in this household")
    if target.id == actor.id:
        raise HTTPException(status_code=400,
                            detail="You cannot deactivate your own login.")
    target.is_active = False
    db.commit()
    return _view(target)
