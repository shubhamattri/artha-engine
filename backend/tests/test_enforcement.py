"""The gate itself, tested through a mounted app rather than by calling the dependency.

Testing the dependency in isolation proves the function works. Testing through a router proves it is
actually WIRED, which is the failure mode that matters: a correct guard nobody calls protects
nothing.
"""

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from auth.permissions import PermissionRule, enforce_permission
from auth.startup_check import assert_every_route_declared
from auth.roles import Level, Role
from models.tenant import Household
from models.user import User
from models.user_permission import UserPermission


def _app(db_session, user: User) -> TestClient:
    """A tiny app carrying the real dependency, with two routes declared in the real registry.

    /api/debt (view) and DELETE /api/debt/{loan_id} (edit) are used because they are already in the
    registry, so this exercises the same lookup the production app does rather than a stand-in.
    """
    from api.deps import get_optional_user
    from database import get_db

    router = APIRouter()

    @router.get("/debt")
    def read_debt():
        return {"ok": "read"}

    @router.delete("/debt/{loan_id}")
    def delete_loan(loan_id: int):
        return {"ok": "deleted"}

    app = FastAPI()
    app.include_router(router, prefix="/api", dependencies=[Depends(enforce_permission)])
    # The same call main.py makes: assert every route is declared, and stamp each with its rule.
    # An app that skips it denies everything, which is the intended default — see
    # test_an_undeclared_route_is_denied_rather_than_allowed, which deliberately does not bind.
    assert_every_route_declared(app, min_routes=0)
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_optional_user] = lambda: user
    return TestClient(app)


def _user(db, hid: int, name: str, role: Role, grants: dict[str, Level] | None = None) -> User:
    u = User(household_id=hid, email=f"{name}@x.test", username=name, name=name,
             password_hash="x", role=role.value)
    db.add(u)
    db.commit()
    for key, level in (grants or {}).items():
        db.add(UserPermission(user_id=u.id, key=key, level=int(level)))
    db.commit()
    db.refresh(u)
    return u


@pytest.fixture()
def household(db_session) -> Household:
    h = Household(name="enforce", cycle_start_day=1)
    db_session.add(h)
    db_session.commit()
    return h


def test_a_member_with_no_grant_cannot_even_read(db_session, household):
    u = _user(db_session, household.id, "bare", Role.member)
    assert _app(db_session, u).get("/api/debt").status_code == 403


def test_a_viewer_can_read_but_not_write(db_session, household):
    u = _user(db_session, household.id, "viewer", Role.member, {"debt": Level.view})
    c = _app(db_session, u)
    assert c.get("/api/debt").status_code == 200
    assert c.delete("/api/debt/1").status_code == 403


def test_an_editor_can_do_both(db_session, household):
    u = _user(db_session, household.id, "editor", Role.member, {"debt": Level.edit})
    c = _app(db_session, u)
    assert c.get("/api/debt").status_code == 200
    assert c.delete("/api/debt/1").status_code == 200


def test_a_grant_on_another_key_does_not_open_this_one(db_session, household):
    """The failure that makes a permission system decorative: any grant behaving like every grant."""
    u = _user(db_session, household.id, "wrongkey", Role.member, {"wealth": Level.edit})
    assert _app(db_session, u).get("/api/debt").status_code == 403


def test_an_admin_needs_no_grants(db_session, household):
    c = _app(db_session, _user(db_session, household.id, "adm", Role.admin))
    assert c.get("/api/debt").status_code == 200
    assert c.delete("/api/debt/1").status_code == 200


def test_a_stale_none_grant_cannot_lock_an_admin_out(db_session, household):
    u = _user(db_session, household.id, "adm2", Role.admin, {"debt": Level.none})
    assert _app(db_session, u).delete("/api/debt/1").status_code == 200


def test_the_denial_names_what_is_needed_without_revealing_whether_the_row_exists(db_session, household):
    """403 before the endpoint body runs, so a denied caller cannot probe for which ids exist by
    reading 404-versus-403 off the response."""
    u = _user(db_session, household.id, "prober", Role.member, {"debt": Level.view})
    r = _app(db_session, u).delete("/api/debt/99999999")
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert "debt" in detail and "edit" in detail
    assert "not found" not in detail.lower()


def test_an_undeclared_route_is_denied_rather_than_allowed(db_session, household):
    """Belt and braces behind the boot check. If a route ever reaches the request path without a
    declaration, the answer must be no."""
    from api.deps import get_optional_user
    from database import get_db

    router = APIRouter()

    @router.get("/not-in-the-registry")
    def orphan():
        return {}

    app = FastAPI()
    app.include_router(router, prefix="/api", dependencies=[Depends(enforce_permission)])
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_optional_user] = lambda: _user(
        db_session, household.id, "orphanuser", Role.admin)

    # Deliberately NOT bound — this is the belt-and-braces case where a route somehow reaches the
    # request path with no declaration. Even for an admin, the answer is no.
    assert TestClient(app).get("/api/not-in-the-registry").status_code == 403


def test_a_rule_compares_levels_rather_than_equality():
    """`edit` satisfies a `view` requirement. If this ever became `==`, every viewer route would
    reject its own editors."""
    assert Level.edit >= PermissionRule("debt", Level.view).level


def test_auth_me_publishes_the_resolved_permissions(db_session, household):
    """The frontend must never re-implement role-plus-grants. If it did, the UI and the server would
    disagree and a person would be shown controls the server then refuses."""
    from api.auth import me

    u = _user(db_session, household.id, "publisher", Role.member,
              {"debt": Level.view, "budget": Level.edit})
    body = me(user=u)

    assert body["role"] == "member"
    assert body["household_id"] == household.id
    assert body["permissions"]["debt"] == int(Level.view)
    assert body["permissions"]["budget"] == int(Level.edit)
    assert body["permissions"]["wealth"] == int(Level.none)
    # Every key present, so the UI never has to handle a missing one.
    from auth.permissions import KEYS
    assert set(body["permissions"]) == set(KEYS)


def test_auth_me_never_leaks_the_password_hash(db_session, household):
    from api.auth import me

    u = _user(db_session, household.id, "nohash", Role.admin)
    assert "password_hash" not in me(user=u)
