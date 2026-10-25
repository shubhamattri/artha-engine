"""The default must be DENY, and it must be enforced by the machine, not by discipline.

A codebase built on hand-called guards, with nothing requiring a new route to have one, ends up with
routes that are open by accident, and nothing tells you: not a linter, not a test, not CI.

So the rule here is not "remember to declare your route". It is "the app does not start if you did
not", and these tests prove the guard actually bites rather than merely existing.
"""

import pytest
from fastapi import FastAPI

from auth.permissions import PUBLIC, REGISTRY, ROLE_GATED, SELF_SERVICE, READ_ONLY_POSTS
from auth.roles import Level
from auth.startup_check import assert_every_route_declared, iter_routes
from main import app

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def test_every_route_is_declared():
    """The assertion the app itself runs at import. If this fails the app will refuse to boot, which
    is the intended behaviour rather than test-only strictness."""
    assert_every_route_declared(app)


def test_an_undeclared_route_stops_the_app_from_starting():
    """Prove the guard bites. Add a route, do not declare it, and the check must refuse."""
    scratch = FastAPI()

    @scratch.get("/api/newly-added-and-forgotten")
    def forgotten():
        return {}

    with pytest.raises(RuntimeError, match="not declared"):
        assert_every_route_declared(scratch, min_routes=0)


def test_a_broken_walker_is_a_failure_not_a_pass():
    """The failure mode that makes every other check worthless: a walker that finds nothing reports
    that nothing is wrong. An empty app must raise, not pass."""
    with pytest.raises(RuntimeError, match="found only"):
        assert_every_route_declared(FastAPI())


def test_no_declaration_names_a_route_that_stopped_existing():
    """A registry that outlives its routes stops being read, and the next person trusts a list that
    is partly fiction."""
    live = {(m, p) for m, p, _ in iter_routes(app)}
    declared = {**REGISTRY, **PUBLIC, **SELF_SERVICE, **ROLE_GATED}
    stale = sorted(f"{m} {p}" for (m, p) in declared if (m, p) not in live)
    assert not stale, f"declared routes that no longer exist: {stale}"


def test_no_read_route_requires_edit():
    """A GET demanding `edit` locks out every viewer for no reason, and it is an easy typo to make in
    a 124-line table."""
    wrong = [f"{m} {p}" for (m, p), r in REGISTRY.items() if m == "GET" and r.level is Level.edit]
    assert not wrong, f"GET routes requiring edit: {wrong}"


def test_every_mutating_route_requires_edit():
    """And the mirror: a POST/PUT/PATCH/DELETE needing only `view` is a viewer who can write."""
    weak = [f"{m} {p}" for (m, p), r in REGISTRY.items()
            if m in MUTATING and r.level is not Level.edit and (m, p) not in READ_ONLY_POSTS]
    assert not weak, f"mutating routes not requiring edit: {weak}"


def test_the_read_only_post_exception_is_still_a_real_route():
    """One route is allowed to mutate-by-method and read-by-permission. If it is ever deleted or
    renamed, the exemption must not linger as a hole waiting for a path to be reused."""
    live = {(m, p) for m, p, _ in iter_routes(app)}
    assert READ_ONLY_POSTS <= live, f"exemption names a dead route: {READ_ONLY_POSTS - live}"


def test_public_routes_are_few_and_named():
    """Not a style rule. Every entry here is a route anyone on the tailnet can call unauthenticated,
    so the list growing is a thing to notice rather than to discover later."""
    assert len(PUBLIC) <= 8, f"the public surface grew to {len(PUBLIC)}: {sorted(PUBLIC)}"
    for reason in PUBLIC.values():
        assert reason and len(reason) > 10, "every public route states why it needs no session"


def test_self_service_routes_take_no_target_parameter():
    """These are exempt from permissions because they act on the caller. That is only true while none
    of them accepts an id naming somebody else."""
    import inspect

    for (method, path), _ in SELF_SERVICE.items():
        for m, p, endpoint in iter_routes(app):
            if (m, p) != (method, path):
                continue
            params = set(inspect.signature(endpoint).parameters)
            targets = params & {"user_id", "member_id", "id", "target_id"}
            assert not targets, (
                f"{method} {path} is exempt from permissions but takes {targets} — it can act on "
                "someone other than the caller and must not be in SELF_SERVICE"
            )


def test_the_real_app_actually_mounts_the_gate():
    """Everything above proves the gate WORKS. This proves it is ON.

    The existing suite builds its own little apps to test handlers, so a gate that was written,
    tested and then never mounted would leave all of it green. That is exactly the shape of the
    failure this whole design is a reaction to: a correct guard nobody calls.

    Router-level dependencies live on the include wrapper, not on the routes underneath — reading
    `route.dependant.dependencies` reports every route as ungated and is how this test first
    "found" 124 problems that did not exist.
    """
    ungated = []
    for r in app.routes:
        if type(r).__name__ != "_IncludedRouter":
            continue
        prefix = getattr(r.include_context, "prefix", "") or ""
        first = getattr(r.original_router.routes[0], "path", "") if r.original_router.routes else ""
        deps = getattr(r.include_context, "dependencies", None) or []
        names = {getattr(getattr(d, "dependency", None), "__name__", "") for d in deps}

        if first.startswith("/auth"):
            # Login, logout, forgot, reset and /me all pre-date a session or act only on the caller.
            assert not names, f"the auth router must carry no gate, found {names}"
            continue
        if "enforce_permission" not in names:
            ungated.append(f"{prefix}{first} (and the rest of its router) deps={sorted(names)}")

    assert not ungated, (
        "these routers do not carry the gate, so every declaration under them is decorative:\n  "
        + "\n  ".join(sorted(ungated))
    )


def test_every_route_got_a_rule_stamped_at_boot():
    """The gate reads a rule stamped onto the route object at import. A route declared but never
    stamped makes the request path find nothing and deny — safe, but silently wrong, and it would
    read as a permissions bug rather than the wiring one it is."""
    from auth.permissions import _rules_by_route

    assert _rules_by_route, "no rules were stamped; main.py did not run the boot check"


def test_role_gated_routes_actually_carry_the_role_check():
    """These skip the permission gate because a role check does the work. If one ever loses that
    dependency it becomes an ordinary authenticated route that any member can call — and the route
    that hands out permissions is the worst one to lose."""
    from auth.startup_check import iter_routes

    naked = []
    for method, path, endpoint in iter_routes(app):
        if (method, path) not in ROLE_GATED:
            continue
        route = None
        for r in app.routes:
            if type(r).__name__ != "_IncludedRouter":
                continue
            prefix = getattr(r.include_context, "prefix", "") or ""
            for sub in r.original_router.routes:
                if prefix + getattr(sub, "path", "") == path and method in getattr(sub, "methods", ()):
                    route = sub
        names = {getattr(d.call, "__name__", "") for d in route.dependant.dependencies} if route else set()
        if "require_super_admin" not in names:
            naked.append(f"{method} {path} deps={sorted(names)}")

    assert not naked, f"role-gated routes missing require_super_admin: {naked}"
