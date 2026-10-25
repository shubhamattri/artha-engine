"""models/__init__ must import EVERY model, because standalone entry points depend on it.

Loan.payments is a relationship declared by the string "LoanPayment", which SQLAlchemy resolves only
once that class has been imported somewhere. The API got it free (main imports api/debt, which
imports the model at module level). scripts/daily_sync.py relied on `import models` instead, and
models/__init__ was missing four modules including that one.

So every Loan query in the sync job raised "failed to locate a name 'LoanPayment'". Each step catches
its own error into a *_err field, so the job exited 0 while doing nothing, every 10 minutes.
"""

import importlib
import pkgutil

import models


def test_every_model_module_is_imported_by_the_package():
    on_disk = {m.name for m in pkgutil.iter_modules(models.__path__)}
    missing = [n for n in sorted(on_disk)
               if f"models.{n}" not in {mod for mod in list(__import__("sys").modules)}]
    assert not missing, f"models/__init__ does not import: {missing}"


def test_every_declared_mapper_resolves():
    """The failure mode itself: a relationship whose target class was never imported blows up on the
    FIRST query, not at import, so it looks fine until something touches the database."""
    from sqlalchemy.orm import configure_mappers
    configure_mappers()          # raises if any string-named relationship cannot be resolved


def test_loan_payments_relationship_resolves_from_a_bare_models_import():
    """Exactly what the sync job does."""
    import subprocess
    import sys
    r = subprocess.run(
        [sys.executable, "-c",
         "import models; from sqlalchemy.orm import configure_mappers; configure_mappers();"
         " from models.household import Loan; print(Loan.payments.property.mapper.class_.__name__)"],
        capture_output=True, text=True, cwd=str(importlib.import_module("models").__path__[0]) + "/..")
    assert r.returncode == 0, r.stderr[-400:]
    assert "LoanPayment" in r.stdout
