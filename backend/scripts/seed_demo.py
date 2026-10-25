"""Load a fictional demo household so a fresh install has something to show.

    cd backend && .venv/bin/python scripts/seed_demo.py

Creates a login (demo@example.com, password from ARTHA_DEMO_PASSWORD or "demo-password"), loads the
loans and cash-flow lines from fixtures/demo_household.json, and writes about 90 days of synthetic card
and bank transactions with generic merchants. Everything comes from a fixed random seed. None of it is
real. Re-running replaces the demo transactions; it never touches another household.
"""

from __future__ import annotations

import hashlib
import os
import random
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auth.security import hash_password  # noqa: E402
from database import SessionLocal  # noqa: E402
from models.tenant import Household  # noqa: E402
from models.transaction import Transaction  # noqa: E402
from models.user import User  # noqa: E402
from parsers.categorizer import classify  # noqa: E402

import seed_household  # noqa: E402  (sibling script; run as `python scripts/seed_demo.py`)

DEMO_EMAIL = "demo@example.com"
DEMO_SOURCE = "demo"
CARD = ("Demo Bank", "Credit Card", "4001")
ACCOUNT = ("Demo Bank", "Account", "5002")

# (description, low, high, times per month) — generic merchants only.
SPEND = [
    ("BLINKIT GROCERY", 300, 1800, 8),
    ("BIGBASKET", 900, 3500, 2),
    ("SWIGGY FOOD ORDER", 250, 900, 6),
    ("ZOMATO", 300, 1100, 4),
    ("UBER INDIA", 150, 600, 6),
    ("INDIAN OIL FUEL", 1500, 3000, 2),
    ("AMAZON PAY INDIA", 400, 6000, 3),
    ("MYNTRA", 800, 4000, 1),
    ("APOLLO PHARMACY", 200, 1500, 1),
]
MONTHLY = [("NETFLIX", 649), ("SPOTIFY", 119), ("ELECTRICITY BILL PAYMENT", 2400)]


def _fp(*parts: object) -> str:
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()


def _txn(hid: int, day: date, amount: float, desc: str, direction: str, src: tuple, n: int) -> Transaction:
    category, confidence, txn_type = classify(desc, direction)
    bank, card_type, last4 = src
    return Transaction(
        household_id=hid, fingerprint=_fp("demo", hid, day, desc, amount, n), date=day.isoformat(),
        description=desc, merchant=None, source=DEMO_SOURCE, amount=round(amount, 2),
        direction=direction, category=category, confidence=confidence, txn_type=txn_type,
        bank=bank, card_type=card_type, card_last4=last4, stmt_month=day.isoformat()[:7],
    )


def demo_transactions(hid: int, today: date, days: int = 90) -> list[Transaction]:
    rng = random.Random(7)
    start = today - timedelta(days=days)
    rows: list[Transaction] = []
    n = 0
    month = date(start.year, start.month, 1)
    while month <= today:
        def on(dom: int) -> date:
            return month.replace(day=min(dom, 28))

        for desc, amount in MONTHLY:
            if start <= on(5) <= today:
                n += 1
                rows.append(_txn(hid, on(5), amount, desc, "debit", CARD, n))
        if start <= on(1) <= today:
            n += 1
            rows.append(_txn(hid, on(1), 200000, "SALARY CREDIT EXAMPLE EMPLOYER", "credit", ACCOUNT, n))
        for desc, low, high, per_month in SPEND:
            for _ in range(per_month):
                day = on(rng.randint(1, 28))
                if start <= day <= today:
                    n += 1
                    rows.append(_txn(hid, day, rng.uniform(low, high), desc, "debit", CARD, n))
        month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    return rows


def main() -> None:
    password = os.environ.get("ARTHA_DEMO_PASSWORD", "demo-password")
    db = SessionLocal()
    try:
        household = db.query(Household).order_by(Household.id).first()
        if household is None:
            household = Household(name="Demo Household")
            db.add(household)
            db.commit()
        hid = household.id

        user = db.query(User).filter(User.email == DEMO_EMAIL).first()
        if user is None:
            user = User(email=DEMO_EMAIL, username="demo", name="Demo User", household_id=hid,
                        role="super_admin", password_hash=hash_password(password))
            db.add(user)
        else:
            user.password_hash = hash_password(password)
            user.is_active = True

        db.query(Transaction).filter(Transaction.household_id == hid,
                                     Transaction.source == DEMO_SOURCE).delete()
        rows = demo_transactions(hid, date.today())
        db.add_all(rows)
        db.commit()
    finally:
        db.close()

    seed_household.main([str(seed_household.DEFAULT_FIXTURE)])
    print(f"Demo household {hid}: {len(rows)} synthetic transactions. Log in as {DEMO_EMAIL}.")


if __name__ == "__main__":
    main()
