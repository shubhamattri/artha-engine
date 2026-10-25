"""The question this file exists to answer: if a household adds a card from the UI tomorrow, does the
rest of the pipeline actually pick it up — or does it just create a row nobody reads?

Registering a card is worthless if SMS capture still filters on a hardcoded bank list, because the
messages are dropped in SQL before anything downstream ever sees them.
"""

from models.account import Account
from models.tenant import Household


def _hh(db):
    h = Household(name="H")
    db.add(h)
    db.commit()
    return h


def test_registering_a_card_makes_its_sms_visible(db_session, monkeypatch):
    """Add an account for a bank the code has never heard of; its SMS must become readable."""
    import services.sms_capture as sc

    h = _hh(db_session)
    db_session.add(Account(household_id=h.id, bank="Fictional Bank", kind="card",
                           last4="4321", holder="A", sms_senders="FICBNK"))
    db_session.commit()

    captured: dict = {}

    def fake_query(sql):
        captured["sql"] = sql
        return []

    class FakeCon:
        def execute(self, sql):
            return fake_query(sql)

        def close(self):
            pass

    monkeypatch.setattr(sc, "_chat_dbs", lambda: ["/fake/chat.db"])
    monkeypatch.setattr(sc.sqlite3, "connect", lambda *a, **k: FakeCon())

    sc.read_bank_sms(since_days=30, db=db_session, household_id=h.id)
    # The generated SQL must be looking for this household's registered sender.
    assert "FICBNK" in captured["sql"], (
        "a registered card's SMS sender is not in the query — the card was added but its messages "
        "are still being dropped before anything can read them"
    )


def test_built_in_banks_still_work_when_no_accounts_registered(db_session, monkeypatch):
    """A household that has registered nothing must still capture the common banks, so existing
    installs and first-run users are not left with an empty pipeline."""
    import services.sms_capture as sc

    h = _hh(db_session)
    captured: dict = {}

    class FakeCon:
        def execute(self, sql):
            captured["sql"] = sql
            return []

        def close(self):
            pass

    monkeypatch.setattr(sc, "_chat_dbs", lambda: ["/fake/chat.db"])
    monkeypatch.setattr(sc.sqlite3, "connect", lambda *a, **k: FakeCon())

    sc.read_bank_sms(since_days=30, db=db_session, household_id=h.id)
    assert "HDFC" in captured["sql"]
    assert "OTP" in captured["sql"]          # the OTP exclusion must survive


def test_a_registered_otp_sender_is_still_refused(db_session, monkeypatch):
    """A user must not be able to register an OTP sender and start ingesting credentials."""
    import services.sms_capture as sc

    h = _hh(db_session)
    db_session.add(Account(household_id=h.id, bank="X", kind="card", last4="1111",
                           sms_senders="XBANKOTP"))
    db_session.commit()

    captured: dict = {}

    class FakeCon:
        def execute(self, sql):
            captured["sql"] = sql
            return []

        def close(self):
            pass

    monkeypatch.setattr(sc, "_chat_dbs", lambda: ["/fake/chat.db"])
    monkeypatch.setattr(sc.sqlite3, "connect", lambda *a, **k: FakeCon())
    sc.read_bank_sms(since_days=30, db=db_session, household_id=h.id)
    # It may appear in the allow-list, but the OTP block must still exclude it.
    assert "NOT LIKE '%OTP%'" in captured["sql"]
