"""Refuse to start if any route is undeclared.

A crash on boot is a strange-feeling design choice, so the reasoning is worth stating plainly: the
alternative is a route that silently answers everyone. That is not hypothetical: it is what happens
when authorisation is a guard hand-called at every site and nothing checks coverage.

The rule here is therefore not "remember to declare your route". It is "the app does not start if
you did not", and the error names the route.
"""

from fastapi import FastAPI
from fastapi.routing import APIRoute

from auth.permissions import (EXEMPT, PUBLIC, REGISTRY, ROLE_GATED, SELF_SERVICE,
                              _rules_by_route)

# Not API surface: the SPA catch-all serves the built frontend, and FastAPI's own docs routes.
SKIP_PATHS = {"/{full_path:path}", "/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect"}

# The app had 136 routes when this was written. The floor exists so that a FastAPI upgrade changing
# how routes are stored fails loudly here rather than quietly walking five of them and declaring
# everything fine — which an earlier version of this walker did, finding 2 of 135.
MIN_EXPECTED_ROUTES = 120


def iter_routes(app: FastAPI):
    """(method, full path, endpoint) for every route, however it is mounted.

    `app.routes` mixes plain `APIRoute` objects with `_IncludedRouter` wrappers, and only the wrapper
    knows its own prefix. Both shapes are walked.
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


def assert_every_route_declared(app: FastAPI, *, min_routes: int = MIN_EXPECTED_ROUTES) -> None:
    """`min_routes` is a parameter so both guards stay independently testable: a scratch app built to
    prove the undeclared-route check works has one route, and would otherwise trip the undercount
    check first and never exercise the thing under test."""
    found = list(iter_routes(app))
    if len(found) < min_routes:
        raise RuntimeError(
            f"the route walker found only {len(found)} routes, expected at least "
            f"{min_routes}. Fix the walker in auth/startup_check.py — an undercount here "
            "would let undeclared routes through by simply not looking at them."
        )

    undeclared = [
        f"{method} {path} -> {endpoint.__name__}"
        for method, path, endpoint in found
        if (method, path) not in REGISTRY
        and (method, path) not in PUBLIC
        and (method, path) not in SELF_SERVICE
        and (method, path) not in ROLE_GATED
    ]
    if undeclared:
        raise RuntimeError(
            "these routes are not declared in auth/permissions.py:\n  "
            + "\n  ".join(sorted(undeclared))
            + "\n\nAdd each to REGISTRY with its permission key and level, or to PUBLIC / "
              "SELF_SERVICE with the reason it needs none. Routes are denied until declared, and "
              "the app will not start while any is missing."
        )

    _stamp_rules(app)


def _stamp_rules(app: FastAPI) -> None:
    """Attach each route's rule to the route object itself.

    The request path cannot rebuild a registry key from what it is given: `route.path` is
    router-RELATIVE (`/debt`), the registry is keyed on the full template (`/api/debt`), and the
    prefix lives on the include wrapper rather than the route. Reconstructing it would be guesswork
    that silently denies everything the moment a prefix changes — which is exactly what the first
    version of this did.

    So the same walk that proves every route is declared also stamps the answer onto the route. One
    source, no matching at request time, and the two cannot drift because they are the same loop.
    """
    for method, path, _endpoint in iter_routes(app):
        rule = REGISTRY.get((method, path))
        marker = rule if rule is not None else EXEMPT
        for r in app.routes:
            if isinstance(r, APIRoute) and r.path == path and method in r.methods:
                _rules_by_route.setdefault(id(r), {})[method] = marker
            elif type(r).__name__ == "_IncludedRouter":
                prefix = getattr(r.include_context, "prefix", "") or ""
                for sub in r.original_router.routes:
                    if (isinstance(sub, APIRoute) and prefix + sub.path == path
                            and method in sub.methods):
                        _rules_by_route.setdefault(id(sub), {})[method] = marker
