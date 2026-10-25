"""Seed a household's loans + cash-flow lines from a JSON fixture (re-runnable).

The figures live in the fixture, never in this file: a household's loan book and
salaries are personal data and must not ship in the repo.

    python scripts/seed_household.py                     # bundled fictional demo
    python scripts/seed_household.py path/to/mine.json    # your own (gitignored)

Loans are stored as INPUTS only (principal, rate, tenure, disbursement) — EMI,
balance, months-left are computed by services.loan_math, never stored. Cash-flow
lines (income / fixed / savings) are editable starting values you maintain in-app.
"""

import json
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal  # noqa: E402
from models.household import CashflowLine, Loan  # noqa: E402

DEFAULT_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "demo_household.json"

LOAN_REQUIRED = ("name", "lender", "holder", "category", "original_principal",
                 "annual_rate_pct", "tenure_months", "disbursement_date")
LOAN_OPTIONAL = ("extra_principal", "rate_type", "loan_type", "flexi_terms",
                 "emi_override", "closed", "closed_on", "foir_exempt",
                 "serviced_by_household", "expected_monthly_payment", "notes")
CASHFLOW_REQUIRED = ("kind", "label", "amount_inr")
CASHFLOW_OPTIONAL = ("holder", "active", "start_date", "end_date")
CASHFLOW_KINDS = ("income", "fixed", "saving")
DATE_FIELDS = ("disbursement_date", "closed_on", "start_date", "end_date")


def _clean(row: dict, required: tuple, optional: tuple, where: str) -> dict:
    missing = [f for f in required if row.get(f) is None]
    if missing:
        raise SystemExit(f"{where}: missing required field(s) {', '.join(missing)} in {row!r}")
    unknown = set(row) - set(required) - set(optional)
    if unknown:
        raise SystemExit(f"{where}: unknown field(s) {', '.join(sorted(unknown))} in {row!r}")
    out = {f: row[f] for f in required + optional if f in row}
    for f in DATE_FIELDS:
        if isinstance(out.get(f), str):
            out[f] = date.fromisoformat(out[f])
    return out


def load_fixture(path: Path) -> tuple[int, list[dict], list[dict]]:
    if not path.exists():
        raise SystemExit(f"fixture not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: expected a JSON object at the top level")

    household_id = int(data.get("household_id", 1))
    loans = [_clean(r, LOAN_REQUIRED, LOAN_OPTIONAL, f"{path.name} loans[{i}]")
             for i, r in enumerate(data.get("loans", []))]
    cashflow = [_clean(r, CASHFLOW_REQUIRED, CASHFLOW_OPTIONAL, f"{path.name} cashflow[{i}]")
                for i, r in enumerate(data.get("cashflow", []))]
    for i, row in enumerate(cashflow):
        if row["kind"] not in CASHFLOW_KINDS:
            raise SystemExit(f"{path.name} cashflow[{i}]: kind must be one of {CASHFLOW_KINDS}")
    return household_id, loans, cashflow


def main(argv: list[str] | None = None) -> None:
    args = [a for a in (sys.argv[1:] if argv is None else argv) if not a.startswith("--")]
    path = Path(args[0]).expanduser() if args else DEFAULT_FIXTURE
    household_id, loans, cashflow = load_fixture(path)

    db = SessionLocal()
    try:
        # Scoped to one household so seeding a fixture never wipes another tenant's rows.
        db.query(Loan).filter(Loan.household_id == household_id).delete()
        db.query(CashflowLine).filter(CashflowLine.household_id == household_id).delete()
        for row in loans:
            db.add(Loan(household_id=household_id, **row))
        for row in cashflow:
            db.add(CashflowLine(household_id=household_id, **row))
        db.commit()
        print(f"Seeded {len(loans)} loans + {len(cashflow)} cash-flow lines "
              f"into household {household_id} from {path.name}.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
