"""Every route must be able to scope to a household, or be on the public list — by name.

Several endpoints operate on every row in the database regardless of who is signed in. That is
invisible with one household and a cross-tenant breach with two. Those endpoints are today's bug;
the absence of this test is why there would be another one next month.

Two things this test learned the hard way, both worth keeping:

**It checks the endpoint's SIGNATURE, not its dependency tree.** `get_current_household` appearing
somewhere in a route's resolved dependencies proves nothing: a router-level dependency puts it on
every route underneath, including the ones whose function body never sees it and cannot filter. A
`household` parameter on the function is the weakest thing that is still evidence the endpoint can
scope its queries. It is not proof that it does — see `test_tenant_isolation.py` for that.

**It asserts it found a plausible number of routes.** The first version of this file used
`isinstance(r, APIRoute)` over `app.routes`, which in this FastAPI version returns the four docs
routes and one healthcheck, because `include_router` stores a `_IncludedRouter` wrapper rather than
flattening. It enumerated 2 routes out of 135 and reported almost everything as fine. A test that
silently measures nothing is worse than no test, so the count is now asserted.
"""

import inspect

from fastapi.routing import APIRoute

from main import app

# Routes that legitimately need no household. Each entry states WHY, because in a year the reason is
# the only thing that lets someone judge whether it is still true.
PUBLIC_ROUTES = {
    ("POST", "/api/auth/login"): "no session exists yet",
    ("POST", "/api/auth/logout"): "clears a cookie; nothing read",
    ("POST", "/api/auth/forgot-password"): "identified by email, pre-session",
    ("POST", "/api/auth/reset-password"): "identified by token, pre-session",
    ("GET", "/api/auth/me"): "self-scoped by session, returns only the caller",
    ("POST", "/api/auth/change-password"): "self-scoped by session, no target parameter",
    ("PATCH", "/api/profile"): "self-scoped by session, no target parameter",
    ("GET", "/api/config"): ("brand name, wordmark, currency and locale only. The login screen "
                             "renders the wordmark before any session exists. The cycle fields it "
                             "used to carry moved to /household/cycle, which knows whose cycle it is"),
    ("GET", "/api/supported"): "static list of parseable statement formats",
    ("GET", "/api/liveness"): "process healthcheck, no data",
}

# Not API surface: the SPA catch-all serves the built frontend, and FastAPI's own docs routes.
SKIP_PATHS = {"/{full_path:path}", "/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect"}

# The app had 135 endpoints when this was written. The floor exists so that a FastAPI upgrade which
# changes how routes are stored fails here loudly, instead of quietly enumerating five of them and
# reporting success.
MIN_EXPECTED_ROUTES = 120


def _routes():
    """(method, full path, endpoint function) for every route, however it is mounted.

    `app.routes` mixes plain `APIRoute` objects with `_IncludedRouter` wrappers, and only the
    wrapper knows its own prefix. Both shapes are walked.
    """
    for r in app.routes:
        if isinstance(r, APIRoute):
            if r.path in SKIP_PATHS:
                continue
            for method in sorted(r.methods - {"HEAD", "OPTIONS"}):
                yield method, r.path, r.endpoint
        elif type(r).__name__ == "_IncludedRouter":
            prefix = getattr(r.include_context, "prefix", "") or ""
            for sub in r.original_router.routes:
                if not isinstance(sub, APIRoute) or (prefix + sub.path) in SKIP_PATHS:
                    continue
                for method in sorted(sub.methods - {"HEAD", "OPTIONS"}):
                    yield method, prefix + sub.path, sub.endpoint


def test_the_walker_actually_found_the_routes():
    """Guards every other assertion in this file. Without it, a change to how FastAPI stores routes
    turns the whole suite into a no-op that passes."""
    found = list(_routes())
    assert len(found) >= MIN_EXPECTED_ROUTES, (
        f"only walked {len(found)} routes, expected at least {MIN_EXPECTED_ROUTES}. "
        "The route walker is broken, not the app — fix `_routes()` before trusting anything below."
    )


def test_every_route_can_scope_to_a_household_or_is_explicitly_public():
    unscoped = []
    for method, path, endpoint in _routes():
        if (method, path) in PUBLIC_ROUTES:
            continue
        if "household" in inspect.signature(endpoint).parameters:
            continue
        unscoped.append(f"{method} {path} ({endpoint.__name__})")

    assert not unscoped, (
        "these endpoints touch data with no household to filter on:\n  "
        + "\n  ".join(sorted(unscoped))
        + "\n\nAdd `household: Household = Depends(get_current_household)` to the signature and "
          "filter every query on it, or add the route to PUBLIC_ROUTES with the reason it needs "
          "no tenant."
    )


def test_the_public_list_has_no_stale_entries():
    """An allow-list that outlives its routes stops being read. If a path is renamed, the old entry
    silently keeps permitting nothing, and the next reader trusts a list that is partly fiction."""
    live = {(m, p) for m, p, _ in _routes()}
    stale = sorted(f"{m} {p}" for m, p in PUBLIC_ROUTES if (m, p) not in live)
    assert not stale, f"PUBLIC_ROUTES names routes that no longer exist: {stale}"
