"""Exactly one super admin per household, enforced by the database.

An application-level "only one" check loses races and is bypassed by every script that writes the
table directly. A partial unique index cannot be.
"""

import pytest
from sqlalchemy.exc import IntegrityError

from auth.roles import Role
from models.tenant import Household
from models.user import User


def _user(hid: int, name: str, role: Role | None = None) -> User:
    kw = {"role": role.value} if role else {}
    return User(household_id=hid, email=f"{name}@x.test", username=name, name=name,
                password_hash="x", **kw)


def _household(db, name: str) -> Household:
    h = Household(name=name, cycle_start_day=1)
    db.add(h)
    db.commit()
    return h


def test_a_second_super_admin_in_the_same_household_is_rejected(db_session):
    h = _household(db_session, "one-super")
    db_session.add(_user(h.id, "first", Role.super_admin))
    db_session.commit()

    db_session.add(_user(h.id, "second", Role.super_admin))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_two_households_may_each_have_their_own_super_admin(db_session):
    """The index is partial AND per household. One super admin each, not one in the whole table."""
    a = _household(db_session, "hh-a")
    b = _household(db_session, "hh-b")
    db_session.add_all([_user(a.id, "a-super", Role.super_admin),
                        _user(b.id, "b-super", Role.super_admin)])
    db_session.commit()      # no exception


def test_many_admins_are_fine(db_session):
    h = _household(db_session, "many-admins")
    db_session.add_all([_user(h.id, "adm1", Role.admin), _user(h.id, "adm2", Role.admin),
                        _user(h.id, "adm3", Role.admin)])
    db_session.commit()      # no exception


def test_a_new_user_is_a_member_until_someone_decides_otherwise(db_session):
    """The default has to be the least powerful role. A new family member who arrives as an admin
    because nobody set a column is the failure this whole piece of work exists to prevent."""
    h = _household(db_session, "defaults")
    u = _user(h.id, "newcomer")
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    assert u.role == Role.member.value
