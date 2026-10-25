"""Every call to a household-scoped service must actually pass the household.

Making `household_id` keyword-only and required was the right shape — an unscoped call is a
cross-tenant read, and a default would let one happen silently. But "required" is only enforced when
the line runs, and two call sites inside `sync_all` were missed when the signatures changed. Both
raised TypeError at runtime, so `POST /transactions/sync` answered 500 to every press of the Sync
button in the app for as long as it took someone to notice and say so.

Nothing caught it. The suite has no test that runs `sync_all` end to end, the invariants check data
rather than code paths, and the deploy gate is green because every test that exists passes.

So this walks the AST instead of waiting for the line to execute. It is the cheapest possible
version of "did the change get swept everywhere", and it is exactly the sweep that was skipped:
grep-and-fix-the-one-you-found, when there were two.
"""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
SKIP = {".venv", "alembic", "__pycache__", "node_modules"}


def _python_files() -> list[Path]:
    return sorted(p for p in BACKEND.rglob("*.py")
                  if not any(part in SKIP for part in p.parts))


def _functions_requiring_household(files: list[Path]) -> dict[str, str]:
    """name -> where it is defined, for every function with a required keyword-only household_id.

    A default of any kind disqualifies it: `household_id: int | None = None` is the deliberate
    "this may run unattended" shape (daily_sync calls those), and it is not what this guards.
    """
    found: dict[str, str] = {}
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
                if arg.arg == "household_id" and default is None:
                    found[node.name] = f"{path.relative_to(BACKEND)}:{node.lineno}"
    return found


def test_no_call_omits_a_required_household_id():
    files = _python_files()
    required = _functions_requiring_household(files)
    assert len(required) >= 10, (
        f"only {len(required)} scoped functions found — the AST walk is broken, and a walk that "
        "finds nothing reports no problems"
    )

    missing = []
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (node.func.attr if isinstance(node.func, ast.Attribute)
                    else getattr(node.func, "id", None))
            if name not in required:
                continue
            keywords = {k.arg for k in node.keywords}
            # `**kwargs` may carry it; that is a forwarding call, not an omission.
            if "household_id" in keywords or None in keywords:
                continue
            missing.append(
                f"{path.relative_to(BACKEND)}:{node.lineno} calls {name}() with no household_id "
                f"(defined at {required[name]})"
            )

    assert not missing, (
        "these calls would raise TypeError the moment they run:\n  "
        + "\n  ".join(sorted(missing))
        + "\n\nPass household_id. If the caller genuinely has no household — an unattended job — "
          "then the function it needs is the one whose household_id defaults to None, not this one."
    )
