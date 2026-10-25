"""The one assertion the whole layer rests on: a real request, refused before it reads anything.

Everything else in this area is tested a level up — the resolver turns a role plus grants into a
map, the registry names a key per route, the boot check proves none is missing. All of that can be
green while the gate is not actually mounted, or is mounted and never consulted, and the app would
look correct in the tests and be wide open in production.

So this goes through the HTTP stack against real routers.
"""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from auth.permissions import enforce_permission
from auth.roles import Level, Role
from database import get_db
from models.tenant import Household
from models.user import User
from models.user_permission import UserPermission


@pytest.fixture()
def hh(db_session) -> Household:
    h = Household(name="gated", cycle_start_day=1)
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


@pytest.fixture()
def app_for(db_session, hh):
    """A client acting as one user, over the same routers and the same gate main.py mounts."""
    from api import debt, meta
    from api.deps import get_current_household, get_current_user, get_optional_user
    from auth.startup_check import _stamp_rules

    def _make(user: User | None) -> TestClient:
        app = FastAPI()
        app.include_router(meta.router, prefix="/api",
                           dependencies=[Depends(enforce_permission)])
        app.include_router(debt.router, prefix="/api",
                           dependencies=[Depends(get_current_user), Depends(enforce_permission)])
        app.dependency_overrides[get_db] = lambda: db_session
        app.dependency_overrides[get_current_household] = lambda: hh
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_optional_user] = lambda: user
        # Same stamping main.py does at boot. Rules live on the route object because `route.path` is
        # router-relative and the registry is keyed on the full template.
        _stamp_rules(app)
        return TestClient(app)

    return _make


def _grant(db, user: User, key: str, level: Level) -> None:
    db.add(UserPermission(user_id=user.id, key=key, level=int(level)))
    db.commit()
    db.refresh(user)


def test_a_member_with_no_grants_is_refused_a_read(app_for, db_session, hh):
    """Default DENY, not default view. A new family member sees nothing until someone decides,
    and the decision is a row in a table rather than an absence nobody notices."""
    r = app_for(_user(db_session, hh.id, "newcomer", Role.member)).get("/api/debt")
    assert r.status_code == 403
    assert "debt" in r.json()["detail"], "the refusal names what they would need"


def test_a_grant_of_view_opens_the_read_and_not_the_write(app_for, db_session, hh):
    """The two halves of `view` — it has to be enough for GET and not enough for anything else,
    or the level is decorative."""
    u = _user(db_session, hh.id, "viewer", Role.member)
    _grant(db_session, u, "debt", Level.view)
    client = app_for(u)

    assert client.get("/api/debt").status_code == 200
    assert client.post("/api/debt", json={"name": "x", "principal_inr": 1}).status_code == 403


def test_edit_implies_view(app_for, db_session, hh):
    """Nobody grants `edit` meaning "can change it but not look at it"."""
    u = _user(db_session, hh.id, "editor", Role.member)
    _grant(db_session, u, "debt", Level.edit)
    assert app_for(u).get("/api/debt").status_code == 200


def test_an_admin_needs_no_grants(app_for, db_session, hh):
    assert app_for(_user(db_session, hh.id, "adm", Role.admin)).get("/api/debt").status_code == 200


def test_a_grant_on_one_key_does_not_leak_to_another(app_for, db_session, hh):
    u = _user(db_session, hh.id, "narrow", Role.member)
    _grant(db_session, u, "wealth", Level.edit)
    assert app_for(u).get("/api/debt").status_code == 403


def test_the_refusal_lands_before_the_endpoint_reads_anything(app_for, db_session, hh):
    """403 for a row that does not exist, not 404.

    Otherwise the difference between the two answers tells an unauthorised caller which loan ids
    are real, which is a slow enumeration of the household's debts by someone who was refused.
    """
    u = _user(db_session, hh.id, "prober", Role.member)
    assert app_for(u).get("/api/debt/999999").status_code == 403


def test_signed_out_is_401_and_not_403(app_for):
    """A different answer to a different question. 403 tells someone with no session that they lack
    a permission, when what they need to be told is to sign in."""
    assert app_for(None).get("/api/debt").status_code == 401


def test_a_public_route_still_answers_before_anyone_signs_in(app_for):
    """The gate covers routers that carry public routes too — /api/config is the login screen's
    branding, and a gate that demanded a session would leave that screen blank."""
    assert app_for(None).get("/api/config").status_code == 200
