"""The Sync button, end to end.

There was no test for this path at all. `auto_merge` and `pair_internal_transfers` both grew a
required `household_id` during the tenancy work and both call sites inside `sync_all` were missed,
so `POST /transactions/sync` answered 500 to every press — while 991 tests passed and the deploy
gate stayed green, because nothing here ever ran the endpoint.

Two things are asserted, and the second is the one that would have caught it:
  * the whole thing answers 200 and reports a summary
  * one broken step does not take the other eight down with it

The external channels (SMS, Gmail) are stubbed. They are not what this is about, they already
degrade on their own, and reaching a real mailbox from a test would make the suite depend on a
network and a credential.
"""

from __future__ import annotations

import pytest

from models.tenant import Household


@pytest.fixture()
def hh(db_session) -> Household:
    h = Household(name="sync", cycle_start_day=1)
    db_session.add(h)
    db_session.commit()
    return h


@pytest.fixture()
def sync_client(db_session, monkeypatch):
    """A client with only the external channels stubbed — every self-heal step runs for real."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api import transactions
    from api.deps import get_current_household
    from database import get_db

    def _make(household):
        monkeypatch.setattr("services.sms_capture.sync_sms",
                            lambda *a, **k: {"ok": True, "added": 0}, raising=False)
        monkeypatch.setattr("services.alert_ingest.sync_alerts",
                            lambda *a, **k: {"ok": True, "added": 0}, raising=False)
        # The AI pass needs a key and a network; it has its own tests.
        monkeypatch.setattr(transactions, "run_categorize",
                            lambda *a, **k: {"ok": True, "updated": 0})
        app = FastAPI()
        app.include_router(transactions.router, prefix="/api")
        app.dependency_overrides[get_db] = lambda: db_session
        app.dependency_overrides[get_current_household] = lambda: household
        return TestClient(app)

    return _make


def test_the_sync_button_answers_200_and_summarises(sync_client, hh):
    """The regression itself: this returned 500 because two calls omitted household_id."""
    r = sync_client(hh).post("/api/transactions/sync", params={"days": 4})
    assert r.status_code == 200, r.text

    body = r.json()
    summary = body["summary"]
    assert summary["failed"] == [], f"a step failed: {summary['failed']}"
    assert summary["new"] == 0 and summary["merged"] == 0

    # Every self-heal step ran. Naming them here means deleting one is a decision, not a diff nobody
    # reads — a sync that quietly stopped merging duplicates still returns 200.
    for step in ("bank_norm", "reattributed", "merchant_norm", "relink",
                 "loan_emis", "refs", "dedup", "transfers"):
        assert step in body, f"{step} did not run"


def test_one_broken_step_does_not_take_the_sync_down(sync_client, hh, monkeypatch):
    """What made the outage total rather than partial.

    SMS and Gmail have always been caught individually; the eight steps after them were not, so a
    single TypeError meant the button did nothing at all. Eight of nine jobs done and a named failure
    beats a 500 for all nine.
    """
    def boom(*_a, **_k):
        raise TypeError("auto_merge() missing 1 required keyword-only argument: 'household_id'")

    monkeypatch.setattr("services.dedup.auto_merge", boom)

    r = sync_client(hh).post("/api/transactions/sync", params={"days": 4})
    assert r.status_code == 200, "one broken step must not 500 the whole sync"

    body = r.json()
    assert body["summary"]["failed"] == ["dedup"]
    assert body["dedup"]["ok"] is False
    assert "household_id" in body["dedup"]["error"], "the reason is reported, not swallowed"
    # The steps after the broken one still ran.
    assert "transfers" in body and body["transfers"].get("ok") is not False
