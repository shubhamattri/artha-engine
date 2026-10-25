"""Nothing may write a category that is not in the taxonomy.

"Others" was dropped in the 21->18 merge and survived as a MERGE key. Two capture paths and a manual
entry route kept writing it, into a bucket no picker, budget or query recognises. Zero rows landed
there, so it was latent rather than active — this keeps it that way.
"""

import re
from pathlib import Path

from services.categories import MERGE, Category

BACKEND = Path(__file__).resolve().parent.parent
VALID = {c.value for c in Category}


def test_every_merge_key_maps_to_a_real_category():
    """A MERGE key is a name that no longer exists. Its TARGET must exist, or the rename is a
    one-way trip into nowhere."""
    for old, new in MERGE.items():
        assert new in VALID, f"{old!r} merges into {new!r}, which is not a Category"
        assert old not in VALID, f"{old!r} is both a live category and a merge key"


def test_no_source_file_writes_a_retired_category_name():
    """Catches the exact shape of the bug: a retired name used as a fallback value."""
    retired = set(MERGE) - VALID
    offenders = []
    for f in BACKEND.rglob("*.py"):
        if any(part in {".venv", "tests", "alembic"} for part in f.parts):
            continue
        if f.name == "categories.py":            # the taxonomy names them on purpose
            continue
        text = f.read_text(errors="ignore")
        for name in retired:
            # an assignment or `or` fallback, not a comment or a MERGE lookup
            if re.search(rf'(=|or)\s*["\']{re.escape(name)}["\']', text):
                offenders.append(f"{f.relative_to(BACKEND)} writes {name!r}")
    assert not offenders, "retired category names still written: " + "; ".join(offenders)


def test_quick_add_falls_back_to_a_real_category():
    """The manual 'log a cash spend' route, which is where the live one was."""
    src = (BACKEND / "api" / "transactions.py").read_text()
    m = re.search(r'category = body\.get\("category"\) or (.+)', src)
    assert m, "quick_add's category fallback moved — re-point this test"
    assert "Category." in m.group(1), f"falls back to a literal: {m.group(1)!r}"
