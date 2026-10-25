"""Bulk-ingest a household's full statement set: every credit card + bank account, all months.

Clears transactions, then loads every file routed by name to the right parser. HDFC uses
CSV (skips the duplicate PDF). Global fingerprint dedup so an overlapping/duplicate
statement never double-counts. Classifies each txn.

The card/account inventory lives in a JSON fixture, never in this file: a household's
card last-4s and account list are personal data and must not ship in the repo.

    python scripts/ingest_all.py                      # bundled fictional demo
    python scripts/ingest_all.py path/to/mine.json    # your own (gitignored)

Fixture schema:
    {
      "cc_dir":   "../data/statements",
      "bank_dir": "../data/legacy/BankStatements",
      "credit_cards": [
        {"prefix": "<filename prefix>", "card_type": "...", "last4": "1234",
         "parser": "hdfc_csv|icici_pdf|kotak_pdf|axis_pdf", "ext": ".csv"}
      ],
      "accounts": [
        {"glob": "*1234*.xls", "card_type": "...", "last4": "1234",
         "parser": "account_excel|kotak_bank|icici_bank", "label": "HDFC Savings ••1234"}
      ]
    }
"""

import glob
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal  # noqa: E402
from models.transaction import Transaction  # noqa: E402
from parsers import (  # noqa: E402
    classify, compute_fingerprint,
    parse_hdfc_csv, parse_icici_pdf, parse_kotak_pdf, parse_axis_pdf,
    parse_account_excel, parse_kotak_bank, parse_icici_bank,
)

DEFAULT_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "demo_statements.json"

PARSERS = {
    "hdfc_csv": parse_hdfc_csv,
    "icici_pdf": parse_icici_pdf,
    "kotak_pdf": parse_kotak_pdf,
    "axis_pdf": parse_axis_pdf,
    "account_excel": parse_account_excel,
    "kotak_bank": parse_kotak_bank,
    "icici_bank": parse_icici_bank,
}

CARD_REQUIRED = ("prefix", "card_type", "last4", "parser", "ext")
ACCOUNT_REQUIRED = ("glob", "card_type", "last4", "parser", "label")


def _clean(row: dict, required: tuple, where: str) -> dict:
    missing = [f for f in required if row.get(f) is None]
    if missing:
        raise SystemExit(f"{where}: missing required field(s) {', '.join(missing)} in {row!r}")
    if row["parser"] not in PARSERS:
        raise SystemExit(f"{where}: unknown parser {row['parser']!r} "
                         f"(known: {', '.join(sorted(PARSERS))})")
    return row


def load_fixture(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"fixture not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: expected a JSON object at the top level")
    cards = [_clean(r, CARD_REQUIRED, f"{path.name} credit_cards[{i}]")
             for i, r in enumerate(data.get("credit_cards", []))]
    accounts = [_clean(r, ACCOUNT_REQUIRED, f"{path.name} accounts[{i}]")
                for i, r in enumerate(data.get("accounts", []))]
    return {
        "cc_dir": data.get("cc_dir", "../data/statements"),
        "bank_dir": data.get("bank_dir", "../data/legacy/BankStatements"),
        "credit_cards": cards,
        "accounts": accounts,
    }


def main(fixture: Path = DEFAULT_FIXTURE) -> None:
    inv = load_fixture(fixture)
    db = SessionLocal()
    seen: set[str] = set()
    counts: dict[str, int] = {}
    try:
        db.query(Transaction).delete()
        db.commit()

        # ---- credit cards ----
        for path in sorted(glob.glob(f"{inv['cc_dir']}/*/*")):
            fname = os.path.basename(path)
            for card in inv["credit_cards"]:
                if fname.startswith(card["prefix"]) and fname.endswith(card["ext"]):
                    card_type, last4 = card["card_type"], card["last4"]
                    try:
                        parsed = PARSERS[card["parser"]](path, card_type, last4) or []
                    except Exception as e:
                        print(f"  ! {fname}: {e}")
                        parsed = []
                    _insert(db, parsed, card_type, last4, seen, counts, f"{card_type} ••{last4}")
                    break

        # ---- bank accounts ----
        for acct in inv["accounts"]:
            ctype, last4 = acct["card_type"], acct["last4"]
            for path in glob.glob(f"{inv['bank_dir']}/{acct['glob']}"):
                try:
                    parsed = PARSERS[acct["parser"]](path, ctype, last4) or []
                except Exception as e:
                    print(f"  ! {os.path.basename(path)}: {e}")
                    parsed = []
                _insert(db, parsed, ctype, last4, seen, counts, acct["label"])

        db.commit()
        print("\n=== loaded ===")
        for k, v in sorted(counts.items()):
            print(f"  {k:34} {v}")
        print(f"  TOTAL {sum(counts.values())}")
    finally:
        db.close()


def _insert(db, parsed, card_type, last4, seen, counts, label):
    n = 0
    batch: dict[str, int] = {}
    for t in parsed:
        base = compute_fingerprint(t)
        seq = batch.get(base, 0)
        batch[base] = seq + 1
        fp = compute_fingerprint(t, seq=seq)
        if fp in seen:
            continue
        seen.add(fp)
        category, confidence, txn_type = classify(t.description, t.direction)
        db.add(Transaction(
            fingerprint=fp, date=t.date, description=t.description, amount=t.amount,
            direction=t.direction, category=category, confidence=confidence, txn_type=txn_type,
            bank=t.bank, card_type=card_type, card_last4=last4, stmt_month=t.stmt_month,
        ))
        n += 1
    counts[label] = counts.get(label, 0) + n


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_FIXTURE)
