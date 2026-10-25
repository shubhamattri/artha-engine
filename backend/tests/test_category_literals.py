"""Guards the ONE-source-of-truth rule that services/categories.py claims.

A category written as a bare string literal cannot follow a rename in the enum: the taxonomy moves,
the literal stays, and the comparison silently stops matching — the classifier keeps testing for a
category that no longer exists. This test fails the moment a new literal appears.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from services.categories import Category

BACKEND = Path(__file__).resolve().parent.parent

# categories.py IS the taxonomy. Migrations are frozen history — a rename must never rewrite what a
# past migration actually wrote. Tests state expected values on purpose, so a literal there is signal.
_EXEMPT_FILES = {"services/categories.py"}
_EXEMPT_DIRS = {".venv", "__pycache__", ".git", "tests", "alembic", "data"}

# Modules that still carry literals, frozen at the sweep that cleaned the classifier + capture path.
# This set may only SHRINK. A file not listed here must never introduce one.
_KNOWN_PENDING = {
    "api/categories.py",
    "api/cockpit.py",
    "api/transactions.py",
    "models/budget_plan.py",
    "parsers/categorizer.py",
    "services/commitments.py",
    "services/month_state.py",
    "services/reconcile.py",
}

# The modules this sweep cleaned — they must stay at zero, including inside prompt text, where the
# category list and every example are interpolated from the enum instead of typed out.
_SWEPT = {
    "services/ai_categorize.py",
    "services/alert_ingest.py",
    "services/classifier.py",
    "services/sms_capture.py",
}

_LITERAL = re.compile("|".join(f"['\"]{re.escape(c.value)}['\"]" for c in Category))


def _offenders() -> dict[str, list[str]]:
    """{relative path: ["12: <the offending line>", ...]} for every non-exempt .py file."""
    found: dict[str, list[str]] = {}
    for path in BACKEND.rglob("*.py"):
        rel = path.relative_to(BACKEND).as_posix()
        if _EXEMPT_DIRS & set(rel.split("/")[:-1]) or rel in _EXEMPT_FILES:
            continue
        hits = [f"{n}: {line.strip()}"
                for n, line in enumerate(path.read_text().splitlines(), 1)
                if _LITERAL.search(line)]
        if hits:
            found[rel] = hits
    return found


@pytest.mark.parametrize("module", sorted(_SWEPT))
def test_swept_module_has_no_category_literal(module: str) -> None:
    hits = _offenders().get(module, [])
    assert not hits, (
        f"{module} regained a hardcoded category name — use the Category enum "
        f"(prompts included: interpolate the value):\n  " + "\n  ".join(hits)
    )


def test_no_new_category_literals_anywhere() -> None:
    new = {f: h for f, h in _offenders().items() if f not in _KNOWN_PENDING}
    assert not new, (
        "New hardcoded category name(s). Import Category from services.categories and use "
        "Category.X.value; in an LLM prompt, interpolate it so the prompt cannot drift:\n"
        + "\n".join(f"{f}\n  " + "\n  ".join(h) for f, h in sorted(new.items()))
    )


def test_pending_list_matches_reality() -> None:
    """A file cleaned up but left on the pending list makes the guard weaker than it looks."""
    stale = _KNOWN_PENDING - set(_offenders())
    assert not stale, f"Cleaned up — drop from _KNOWN_PENDING so it is guarded again: {sorted(stale)}"
