"""Add a bank nobody wrote code for — does its money actually arrive, attributed correctly?

This is the end-to-end question for the product: a household adds an account in the UI and expects
its transactions to show up against that account. Two independent things have to hold.

  1. CAPTURE — the sender must reach the SQL filter, or the message is dropped before anything reads it.
  2. ATTRIBUTION — the row must be filed against the registered account, not the "SMS / Account / 0000"
     phantom bucket. Card identity was resolved purely from transaction history, which by definition
     knows nothing about a card that has never been seen, so a newly added account's very first
     transactions were orphaned.
"""

from models.account import Account
from models.tenant import Household
from services.accounts import resolve_card


def _hh(db):
    h = Household(name="H")
    db.add(h)
    db.commit()
    return h


def test_a_brand_new_account_is_attributed_from_the_registration(db_session):
    """No transaction history at all — the accounts table is the only source of truth."""
    h = _hh(db_session)
    db_session.add(Account(household_id=h.id, bank="SBI", kind="account", last4="7788",
                           label="savings", sms_senders="SBIINB"))
    db_session.commit()

    got = resolve_card(db_session, "7788", household_id=h.id)
    assert got == ("SBI", "savings"), f"a registered account must be recognised, got {got}"


def test_a_new_credit_card_gets_a_sensible_type_without_a_label(db_session):
    h = _hh(db_session)
    db_session.add(Account(household_id=h.id, bank="Yes Bank", kind="card", last4="4455"))
    db_session.commit()
    assert resolve_card(db_session, "4455", household_id=h.id) == ("Yes Bank", "Credit Card")


def test_an_unregistered_last4_is_still_refused(db_session):
    """The phantom-card guard must survive: an SMS parser misreading a number must NOT invent a card."""
    h = _hh(db_session)
    assert resolve_card(db_session, "9999", household_id=h.id) is None
    assert resolve_card(db_session, "0000", household_id=h.id) is None
    assert resolve_card(db_session, None, household_id=h.id) is None


def test_one_household_cannot_be_attributed_to_anothers_account(db_session):
    a, b = _hh(db_session), _hh(db_session)
    db_session.add(Account(household_id=a.id, bank="SBI", kind="account", last4="7788"))
    db_session.commit()
    assert resolve_card(db_session, "7788", household_id=a.id) is not None
    assert resolve_card(db_session, "7788", household_id=b.id) is None


def test_capture_and_attribution_hold_together_for_an_unknown_bank(db_session, monkeypatch):
    """Both halves at once: the sender reaches the query AND the last4 resolves."""
    import services.sms_capture as sc

    h = _hh(db_session)
    db_session.add(Account(household_id=h.id, bank="SBI", kind="account", last4="7788",
                           label="savings", sms_senders="SBIINB"))
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

    assert "SBIINB" in captured["sql"]                                   # 1. captured
    assert resolve_card(db_session, "7788", household_id=h.id) == ("SBI", "savings")   # 2. attributed
