"""Managing people — and the four things this must refuse.

The endpoints themselves are unremarkable CRUD. The refusals are the design: each one is a way a
household could be left unable to administer itself, or a way a member could quietly become an
administrator.
"""

import pytest
from fastapi import HTTPException

from api.admin import deactivate_user, list_users, require_super_admin, set_permissions, set_role
from api.admin import GrantIn, GrantsIn, RoleIn
from auth.roles import Level, Role
from models.tenant import Household
from models.user import User
from models.user_permission import UserPermission


@pytest.fixture()
def hh(db_session) -> Household:
    h = Household(name="admin-api", cycle_start_day=1)
    db_session.add(h)
    db_session.commit()
    return h


def _user(db, hid: int, name: str, role: Role) -> User:
    u = User(household_id=hid, email=f"{name}@x.test", username=name, name=name,
             password_hash="x", role=role.value)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def test_only_a_super_admin_gets_through_the_door(db_session, hh):
    for role in (Role.member, Role.admin):
        with pytest.raises(HTTPException) as e:
            require_super_admin(user=_user(db_session, hh.id, f"u-{role.value}", role))
        assert e.value.status_code == 403
    boss = _user(db_session, hh.id, "boss", Role.super_admin)
    assert require_super_admin(user=boss) is boss


def test_listing_shows_resolved_access_not_the_rows_behind_it(db_session, hh):
    """An admin holds no grant rows and total access. Showing the rows would read as 'no
    permissions', which is the opposite of true."""
    boss = _user(db_session, hh.id, "boss", Role.super_admin)
    _user(db_session, hh.id, "adm", Role.admin)
    out = list_users(db=db_session, _=boss, household=hh)

    adm = next(u for u in out["users"] if u["username"] == "adm")
    assert all(v == int(Level.edit) for v in adm["permissions"].values())


def test_a_super_admin_cannot_demote_themselves(db_session, hh):
    """The household would be left with nobody who can grant anything, and no way back in."""
    boss = _user(db_session, hh.id, "boss", Role.super_admin)
    with pytest.raises(HTTPException) as e:
        set_role(boss.id, RoleIn(role="admin"), db=db_session, actor=boss, household=hh)
    assert e.value.status_code == 400
    assert "nobody who can grant" in e.value.detail


def test_a_second_super_admin_is_refused_with_a_sentence_not_a_500(db_session, hh):
    """The unique index would stop it either way. It would surface as an integrity error, which
    reads as a bug rather than a rule."""
    boss = _user(db_session, hh.id, "boss", Role.super_admin)
    other = _user(db_session, hh.id, "other", Role.member)
    with pytest.raises(HTTPException) as e:
        set_role(other.id, RoleIn(role="super_admin"), db=db_session, actor=boss, household=hh)
    assert e.value.status_code == 400
    assert "one super admin per household" in e.value.detail


def test_a_user_in_another_household_is_invisible_rather_than_forbidden(db_session, hh):
    """404, not 403: a super admin has no business learning that a user id exists elsewhere."""
    other_hh = Household(name="elsewhere", cycle_start_day=1)
    db_session.add(other_hh)
    db_session.commit()
    stranger = _user(db_session, other_hh.id, "stranger", Role.member)
    boss = _user(db_session, hh.id, "boss", Role.super_admin)

    with pytest.raises(HTTPException) as e:
        set_role(stranger.id, RoleIn(role="admin"), db=db_session, actor=boss, household=hh)
    assert e.value.status_code == 404


def test_granting_replaces_rather_than_merges(db_session, hh):
    """Send the whole set and get exactly that. A merge makes 'revoke' a different call from 'grant'
    and leaves the caller unable to know the result without re-reading."""
    boss = _user(db_session, hh.id, "boss", Role.super_admin)
    m = _user(db_session, hh.id, "m", Role.member)

    set_permissions(m.id, GrantsIn(grants=[GrantIn(key="debt", level=2),
                                           GrantIn(key="wealth", level=1)]),
                    db=db_session, actor=boss, household=hh)
    out = set_permissions(m.id, GrantsIn(grants=[GrantIn(key="budget", level=1)]),
                          db=db_session, actor=boss, household=hh)

    assert out["permissions"]["budget"] == int(Level.view)
    assert out["permissions"]["debt"] == int(Level.none), "the earlier grant should be gone"
    assert out["permissions"]["wealth"] == int(Level.none)


def test_a_level_zero_grant_stores_no_row(db_session, hh):
    """No row and a stored zero mean the same thing. Keeping one shape means the table cannot
    disagree with itself about what absence means."""
    boss = _user(db_session, hh.id, "boss", Role.super_admin)
    m = _user(db_session, hh.id, "m", Role.member)
    set_permissions(m.id, GrantsIn(grants=[GrantIn(key="debt", level=0)]),
                    db=db_session, actor=boss, household=hh)
    assert db_session.query(UserPermission).filter(UserPermission.user_id == m.id).count() == 0


def test_an_unknown_permission_key_is_refused(db_session, hh):
    boss = _user(db_session, hh.id, "boss", Role.super_admin)
    m = _user(db_session, hh.id, "m", Role.member)
    with pytest.raises(HTTPException) as e:
        set_permissions(m.id, GrantsIn(grants=[GrantIn(key="everything", level=2)]),
                        db=db_session, actor=boss, household=hh)
    assert e.value.status_code == 400 and "everything" in e.value.detail


def test_a_super_admin_cannot_lock_themselves_out(db_session, hh):
    boss = _user(db_session, hh.id, "boss", Role.super_admin)
    with pytest.raises(HTTPException) as e:
        deactivate_user(boss.id, db=db_session, actor=boss, household=hh)
    assert e.value.status_code == 400


def test_deactivating_keeps_the_person_and_removes_the_login(db_session, hh):
    """Their transactions are household history and their member row unlocks statement PDFs. Access
    is the thing being removed, not the person."""
    boss = _user(db_session, hh.id, "boss", Role.super_admin)
    m = _user(db_session, hh.id, "m", Role.member)
    out = deactivate_user(m.id, db=db_session, actor=boss, household=hh)
    assert out["is_active"] is False
    assert db_session.query(User).filter(User.id == m.id).first() is not None
