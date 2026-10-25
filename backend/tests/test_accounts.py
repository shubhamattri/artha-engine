"""Bank-field repair: a garbled bank (whole card string leaked in) normalizes to the clean bank, so the
billing cycle resolves right. Reproducible + idempotent."""

from models.tenant import Household
from models.transaction import Transaction
from services.accounts import normalize_bank, repair_banks


def _t(hid, bank):
    return Transaction(household_id=hid, fingerprint=f"b-{hid}-{bank}", date="2026-06-10",
                       description="x", merchant="x", source="sms", amount=100.0, direction="debit",
                       category="Groceries", txn_type="SPEND", bank=bank, card_type="Select RuPay",
                       card_last4="7788", stmt_month="2026-06")


def test_normalize_bank_substring():
    assert normalize_bank("Axis Select RuPay ••7788") == "Axis"
    assert normalize_bank("HDFCBANK") == "HDFC"
    assert normalize_bank("ICICI") == "ICICI"
    assert normalize_bank("SMS") == "Sms"          # no bank keyword → left as-is (title)


def test_repair_banks_is_idempotent(db_session):
    h = Household(name="B"); db_session.add(h); db_session.commit()
    db_session.add_all([_t(h.id, "Axis Select RuPay ••7788"), _t(h.id, "Axis")]); db_session.commit()
    out = repair_banks(db_session, h.id)
    assert out["changed"] == 1                       # only the garbled one moved
    assert {t.bank for t in db_session.query(Transaction).filter(Transaction.household_id == h.id)} == {"Axis"}
    assert repair_banks(db_session, h.id)["changed"] == 0   # idempotent — nothing left to fix
