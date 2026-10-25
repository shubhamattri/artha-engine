"""The audit trail names a person, and never names the wrong one.

Two failures are worth more than the feature: attributing an edit to whoever happened to be served
last, and attributing a scheduled job's work to a human. Both are silent, and both make the log
worse than having no log, because a wrong name reads as evidence.
"""

from __future__ import annotations

import threading

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from auth.acting_user import acting_user_id, set_acting_user
from auth.permissions import enforce_permission
from auth.roles import Role
from models.audit import TransactionEvent
from models.tenant import Household
from models.user import User
from services.audit import log_event


@pytest.fixture()
def hh(db_session) -> Household:
    h = Household(name="acting", cycle_start_day=1)
    db_session.add(h)
    db_session.commit()
    return h


def _user(db, hid: int, name: str) -> User:
    u = User(household_id=hid, email=f"{name}@x.test", username=name, name=name,
             password_hash="x", role=Role.admin.value)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def test_an_event_records_the_person_who_is_signed_in(db_session, hh):
    me = _user(db_session, hh.id, "me")
    set_acting_user(me.id)
    try:
        log_event(db_session, household_id=hh.id, txn_id=7, action="edit", field="amount",
                  old=1, new=2)
        db_session.commit()
    finally:
        set_acting_user(None)

    row = db_session.query(TransactionEvent).one()
    assert row.user_id == me.id
    assert row.actor == "user", "actor still says what KIND of thing acted"


def test_work_with_no_person_behind_it_stays_anonymous(db_session, hh):
    """A nightly sync has no user. Stamping one would put a name on a change nobody made."""
    log_event(db_session, household_id=hh.id, txn_id=7, action="categorize", actor="sync")
    db_session.commit()
    assert db_session.query(TransactionEvent).one().user_id is None


def test_one_request_cannot_be_attributed_to_another_persons_session(db_session, hh):
    """The whole reason this is a contextvar and not a module global.

    Two people editing at the same moment is the ordinary case for a household app, and a global
    would hand the second request the first one's identity — or the reverse, depending on timing.
    """
    a, b = _user(db_session, hh.id, "a"), _user(db_session, hh.id, "b")
    seen: dict[str, int | None] = {}
    started = threading.Barrier(2)

    def act(user_id: int, tag: str) -> None:
        set_acting_user(user_id)
        started.wait(timeout=5)      # both have set theirs before either reads
        seen[tag] = acting_user_id()

    threads = [threading.Thread(target=act, args=(a.id, "a")),
               threading.Thread(target=act, args=(b.id, "b"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert seen == {"a": a.id, "b": b.id}
    assert acting_user_id() is None, "and neither leaked back out to this thread"


def test_the_gate_is_what_stamps_it(db_session, hh):
    """Not a detail of the audit helper: identity is set once, by the dependency every route
    already carries, so a new mutation gets it without its author doing anything."""
    from api.deps import get_optional_user
    from auth.startup_check import _stamp_rules

    me = _user(db_session, hh.id, "gated")
    app = FastAPI()

    # Both shapes, because they reach the endpoint by different routes: an async endpoint runs in
    # the request's own task, a sync one in a worker thread that copies the context. A gate that is
    # itself sync writes into a copy that is thrown away, and every audit row records nobody.
    @app.get("/whoami-async", dependencies=[Depends(enforce_permission)])
    async def whoami_async():
        return {"id": acting_user_id()}

    @app.get("/whoami-sync", dependencies=[Depends(enforce_permission)])
    def whoami_sync():
        return {"id": acting_user_id()}

    app.dependency_overrides[get_optional_user] = lambda: me
    # Stamping only. The full boot check refuses this app, correctly — /whoami is not declared and
    # nothing undeclared may serve. What is under test is that the gate sets identity BEFORE it
    # decides anything, including on the routes it waves through.
    _stamp_rules(app)

    set_acting_user(None)
    client = TestClient(app)
    assert client.get("/whoami-async").json() == {"id": me.id}
    assert client.get("/whoami-sync").json() == {"id": me.id}
