"""Every endpoint must be reachable from the app, or be on the list — by name, with a reason.

A route nobody calls is not neutral. It is code that is maintained, migrated, permission-declared
and reasoned about, while doing nothing for anyone. Worse, it hides the opposite failure: `POST
/insurance` and `PUT /insurance/{pid}` both existed and neither had a control, so a household could
delete an insurance policy and never add or correct one. That reads to the person using it as the
app being broken, and it survived because nothing was counting.

The check is deliberately crude — it greps the API layer for the path literal. What it proves and
what it does not, stated plainly so nobody reads more into a green run than is there:

  * it proves every backend PATH is named somewhere in `src/api/`
  * it proves every wrapper in `src/api/` is called from somewhere
  * it does NOT distinguish methods. `PUT /windfall/measured-return` being wired satisfies
    `GET /windfall/measured-return` too, because both are the same literal in the source. Matching
    the verb would mean parsing calls rather than grepping them, and the multi-line ones defeat a
    regex — an earlier attempt at exactly that reported eight gaps out of seventeen.
  * it does NOT prove a control is reachable, only that a call site exists

That is the weakest pair of checks that still catches "the backend grew a feature and the UI never
got one", which is the failure actually being prevented.

Where it is skipped, the entry says why, because in a year the reason is the only thing that lets
someone judge whether it is still true.
"""

from __future__ import annotations

import re
from pathlib import Path

from auth.startup_check import iter_routes
from main import app

FRONTEND_SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"

# Not app surface. Each entry states what calls it INSTEAD of the frontend.
NOT_FOR_THE_UI = {
    "/api/liveness": "launchd and deploy.sh probe it; a UI has no use for a liveness check",
    "/api/health": "same, kept as the older name so an existing monitor does not break",
}


def _ts_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.suffix in {".ts", ".tsx"} and p.is_file())


def _frontend_text() -> str:
    """Every line of the API layer, concatenated. One read, not one per route.

    `src/api/` only, and that narrowness is the point. Scanning all of `src/` let a react-router
    `<Link to="/profile">` vouch for the API endpoint `/api/profile`, which is how a completely
    unwired module passed. Screen paths and endpoint paths look identical and mean nothing alike.

    What keeps this from missing calls is the companion test below: nothing outside `src/api/` is
    allowed to reach for HTTP directly.
    """
    return "\n".join(p.read_text(encoding="utf-8", errors="ignore")
                     for p in _ts_files(FRONTEND_SRC / "api"))


def _pattern(path: str) -> re.Pattern:
    """The literal a caller would write, as a regex.

    The client prepends `/api`, so `/api/debt/{loan_id}/payments` is written
    `` `/debt/${id}/payments` `` — one path parameter becomes one template hole whose variable name
    is the caller's business, not ours.

    Matching the whole shape rather than a word is the point. An earlier version matched only the
    last segment, which passed `/api/audit/history` because the word "history" appears somewhere in
    the frontend, and `/api/wallet/spend` because "spend" appears about forty times. A check that
    loose reports eight of seventeen gaps and calls it a pass.
    """
    literal = path[len("/api"):]
    parts = [re.escape(seg) if not seg.startswith("{") else r"\$\{[^}]*\}"
             for seg in literal.split("/")]
    # The end is anchored on what can legally follow a path in source: the closing quote, or the `?`
    # that starts a query string. Without it `/commitments` is satisfied by `/commitments/reconcile`
    # and `/cashflow` by `/cashflow/summary` — a child vouching for a parent it has nothing to do
    # with, which is how two more gaps hid behind endpoints that were genuinely wired.
    return re.compile("/".join(parts) + r"(?=[\"'`?])")


def test_the_frontend_names_every_endpoint():
    routes = sorted({p for _m, p, _e in iter_routes(app)})
    assert len(routes) > 100, f"the walker found only {len(routes)} routes — fix the walker first"

    src = _frontend_text()
    assert "api.get" in src, "read the wrong directory — no frontend source was found"

    missing = []
    for path in routes:
        if path in NOT_FOR_THE_UI:
            continue
        if not _pattern(path).search(src):
            missing.append(path)

    assert not missing, (
        "these endpoints exist and no frontend code names them:\n  "
        + "\n  ".join(missing)
        + "\n\nBuild the control, or delete the endpoint, or add it to NOT_FOR_THE_UI with the "
          "reason it is not app surface. An endpoint nobody can reach is a feature nobody has."
    )


def test_every_api_function_is_actually_called():
    """Closes the other half of the loop: endpoint → api wrapper → something that renders it.

    Without this, "wired" degrades into "a wrapper exists". A `deactivateUser()` that nothing calls
    satisfies the path check above while the household still cannot deactivate anyone — the same
    failure this file is about, one layer along.

    "Called" means referenced anywhere in `src/` besides its own definition AND outside an import
    statement. Imports are stripped first, because a name in an import list is not a use: deleting
    the one call site while leaving the import behind kept `fetchCorrections` looking alive here,
    which is precisely the state this test exists to catch. A wrapper consumed by a hook in the same
    file is still used — the hook is what the screen imports.
    """
    api_dir = FRONTEND_SRC / "api"
    imports = re.compile(r"^import\s[\s\S]*?from\s+['\"][^'\"]+['\"]", re.M)
    everything = "\n".join(imports.sub("", p.read_text(encoding="utf-8", errors="ignore"))
                           for p in _ts_files(FRONTEND_SRC))

    unused = []
    for path in _ts_files(api_dir):
        for name in re.findall(r"^export (?:async )?function (\w+)", path.read_text(), re.M):
            # The definition itself is one of the matches, so one occurrence means nothing calls it.
            if len(re.findall(rf"\b{name}\b", everything)) <= 1:
                unused.append(f"{name}  ({path.name})")

    assert not unused, (
        "these api functions exist and nothing calls them:\n  "
        + "\n  ".join(sorted(unused))
        + "\n\nAn endpoint with a wrapper but no control is still a feature nobody has."
    )


def test_nothing_outside_the_api_layer_reaches_for_http():
    """The reachability check reads `src/api/` alone, so this is what makes that reading complete.

    It is also the codebase's own convention: one place that knows the HTTP surface, so a screen
    cannot quietly invent a call with a hand-built URL. That had already drifted once — the People
    screen was written against `api.get('/admin/users')` inline.
    """
    offenders = [
        str(p.relative_to(FRONTEND_SRC))
        for p in _ts_files(FRONTEND_SRC)
        if "api/" not in str(p.relative_to(FRONTEND_SRC))
        and re.search(r"\bapi\.(get|post|put|patch|delete)\b",
                      p.read_text(encoding="utf-8", errors="ignore"))
    ]
    assert not offenders, (
        "these files call HTTP directly instead of going through src/api/:\n  "
        + "\n  ".join(sorted(offenders))
        + "\n\nMove the call into src/api/. Otherwise nothing can tell which endpoints the app "
          "actually uses, and this file's reachability check stops seeing them."
    )


def test_the_skip_list_has_no_stale_entries():
    """A reason for skipping a route that no longer exists is a reason nobody will ever re-read."""
    live = {p for _m, p, _e in iter_routes(app)}
    stale = sorted(set(NOT_FOR_THE_UI) - live)
    assert not stale, f"NOT_FOR_THE_UI names routes that no longer exist: {stale}"


def test_the_matcher_finds_a_real_route_and_not_an_invented_one():
    """The whole check is one regex over one string, so both directions get proven.

    Without this, a `_frontend_text` returning nothing would fail everything (loud, fine) and one
    returning something enormous would pass everything (silent, not fine). The route walker this
    file borrows from made exactly that mistake once, enumerating 2 routes of 135.
    """
    src = _frontend_text()
    assert _pattern("/api/budget/category-weeks").search(src), "a wired route should be found"
    assert _pattern("/api/no-such-endpoint").search(src) is None


def test_neither_a_parent_nor_a_child_path_vouches_for_the_other():
    """Both directions of the substring trap, which between them hid four gaps.

    `/debt` must not stand in for `/debt/{id}/skip` (a prefix passing for the whole), and
    `/commitments/reconcile` must not stand in for `/commitments` (a longer path containing a
    shorter one).
    """
    parent_only = "api.get('/debt')"
    assert _pattern("/api/debt").search(parent_only)
    assert _pattern("/api/debt/{loan_id}/skip").search(parent_only) is None

    child_only = "api.get('/commitments/reconcile')"
    assert _pattern("/api/commitments/reconcile").search(child_only)
    assert _pattern("/api/commitments").search(child_only) is None


def test_a_query_string_still_counts_as_a_call():
    """`/transactions/sync?days=${d}` is a call to `/transactions/sync`."""
    assert _pattern("/api/transactions/sync").search("api.post(`/transactions/sync?days=${d}`)")
