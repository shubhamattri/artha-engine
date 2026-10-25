"""The demo seeder is synthetic, repeatable, and stays inside the window it was asked for."""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import seed_demo  # noqa: E402

TODAY = date(2026, 6, 15)


def test_same_seed_same_rows():
    a = [(t.date, t.description, t.amount) for t in seed_demo.demo_transactions(1, TODAY)]
    b = [(t.date, t.description, t.amount) for t in seed_demo.demo_transactions(1, TODAY)]
    assert a == b and len(a) > 50


def test_rows_stay_in_the_window_and_are_scoped():
    rows = seed_demo.demo_transactions(7, TODAY, days=90)
    start = (TODAY - timedelta(days=90)).isoformat()
    assert all(start <= t.date <= TODAY.isoformat() for t in rows)
    assert {t.household_id for t in rows} == {7}
    assert {t.source for t in rows} == {"demo"}


def test_fingerprints_are_unique_so_a_reseed_cannot_collide():
    rows = seed_demo.demo_transactions(1, TODAY)
    assert len({t.fingerprint for t in rows}) == len(rows)


def test_salary_is_a_credit_and_spend_is_never_income():
    rows = seed_demo.demo_transactions(1, TODAY)
    salary = [t for t in rows if t.description.startswith("SALARY")]
    assert salary and all(t.direction == "credit" for t in salary)
    assert all(t.txn_type != "INCOME" for t in rows if t.direction == "debit")
    assert all(t.amount > 0 for t in rows)
