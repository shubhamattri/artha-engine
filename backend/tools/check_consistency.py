"""Run the cross-module invariants in services/consistency.py and turn them into an exit code.

They only ever ran when somebody pasted a snippet over SSH, so a net worth off by Rs 6,50,000, a
-22,500 on the main screen and a payoff plan with blank savings all shipped anyway. This makes them
a command `make check` and deploy/deploy.sh can fail on.

    cd backend && .venv/bin/python -m tools.check_consistency [--household all|<id>]

Runs against whatever database the normal app config points at (backend/.env / DB_URL), because it
boots the same app module the server runs.

Exit codes:
    0  every invariant clean (warnings are printed but do not fail)
    1  at least one invariant came back severity "error"
    2  the checks could not be run at all (no database, no household, import failure)
"""

from __future__ import annotations

import argparse
import sys

EXIT_OK = 0
EXIT_INVARIANT_FAILED = 1
EXIT_COULD_NOT_RUN = 2

MARK = {"error": "x", "warn": "!", "ok": "+"}
# Worst first: a wrong number must not be buried under the checks that passed.
ORDER = {"error": 0, "warn": 1, "ok": 2}


def _household_arg(value: str) -> str:
    if value == "all":
        return value
    try:
        int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("--household takes a household id or 'all'") from None
    return value


def _money(v: float | None) -> str:
    return "—" if v is None else f"{v:,.2f}"


def _print_household(name: str, hid: int, report: dict) -> None:
    print(f"\nhousehold {hid} — {name}")
    findings = sorted(report["findings"], key=lambda f: (ORDER.get(f["severity"], 9), f["check"]))
    if not findings:
        print("  (no checks ran)")
    for f in findings:
        print(f"  {MARK.get(f['severity'], '?')} {f['check']:<32} {f['owner']:<24} {f['detail']}")
        if f["expected"] is not None or f["actual"] is not None:
            print(f"      expected {_money(f['expected'])}   actual {_money(f['actual'])}"
                  f"   gap {_money(f['gap'])}")
        if f["fix"] and f["severity"] != "ok":
            print(f"      fix: {f['fix']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools.check_consistency",
        description="Cross-module invariants as a pass/fail gate.")
    parser.add_argument("--household", type=_household_arg, default="all",
                        help="household id to check, or 'all' (default: all)")
    args = parser.parse_args(argv)

    try:
        # Booting the app module registers every SQLAlchemy mapper and loads the same config the
        # server uses; importing a subset leaves relationships unresolvable at query time.
        import main as _app  # noqa: F401
        from database import SessionLocal
        from models.tenant import Household
        from services.consistency import run_all
    except Exception as exc:  # noqa: BLE001 — a gate that cannot import must fail loudly, not pass
        print(f"consistency gate could not start: {exc}", file=sys.stderr)
        return EXIT_COULD_NOT_RUN

    db = SessionLocal()
    try:
        try:
            households = db.query(Household).order_by(Household.id).all()
        except Exception as exc:  # noqa: BLE001
            print(f"consistency gate could not reach the database: {exc}", file=sys.stderr)
            print("is Postgres up? (make up)", file=sys.stderr)
            return EXIT_COULD_NOT_RUN

        if args.household != "all":
            wanted = int(args.household)
            households = [h for h in households if h.id == wanted]
            if not households:
                print(f"no household {wanted} in this database", file=sys.stderr)
                return EXIT_COULD_NOT_RUN
        if not households:
            print("no households in this database — nothing to check", file=sys.stderr)
            return EXIT_COULD_NOT_RUN

        print(f"cross-module invariants — {len(households)} household(s)")
        errors = warns = 0
        for h in households:
            report = run_all(db, h.id)
            _print_household(h.name, h.id, report)
            errors += report["error_count"]
            warns += report["warn_count"]
    finally:
        db.close()

    print(f"\n{errors} error(s), {warns} warning(s)")
    if errors:
        print("FAIL — a module disagrees with the module that owns the number.")
        return EXIT_INVARIANT_FAILED
    print("OK — every cross-module invariant holds.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
