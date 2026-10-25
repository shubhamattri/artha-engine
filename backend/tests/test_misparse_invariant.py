"""The misparse check must fire on the shape that actually got through.

Every failure this session was silent. The ICICI savings statement handed to the card parser raised
nothing -- it produced confident rows with the running balance as the amount and the real amount as
the description. It was found because a person noticed their budget had flipped, which is not a
detection mechanism.
"""

import pytest

from models.tenant import Household
from models.transaction import Transaction
from services.consistency import Report, check_no_misparsed_rows


@pytest.fixture
def _hh(db_session):
    h = Household(name="A"); db_session.add(h); db_session.commit()
    return h


def _add(db, hid, amount, desc):
    db.add(Transaction(household_id=hid, fingerprint=f"fp{amount}{desc}", date="2026-07-13",
                       description=desc, amount=amount, direction="debit", txn_type="SPEND",
                       bank="ICICI", card_type="Credit Card", card_last4="6611",
                       stmt_month="2026-07"))
    db.commit()


def test_it_fires_on_a_balance_read_as_an_amount(db_session, _hh):
    """Amount 7,000.50 with description '749.00' — the columns are swapped."""
    _add(db_session, _hh.id, 7000.50, "749.00")
    r = Report()
    check_no_misparsed_rows(db_session, _hh.id, r)
    assert r.errors, "the check passed on a row whose description is a bare number"
    assert "wrong column" in r.errors[0].detail


def test_it_stays_quiet_on_real_merchant_names(db_session, _hh):
    """The control. A check that fires on healthy data gets ignored, which is worse than no check."""
    for amt, desc in ((340.0, "SWIGGY FOOD"), (2400.0, "AMAZON PAY"),
                      (99.0, "REDEMPTION PROC FEE"), (667.44, "IGST-VPS1000200030004-RATE 18.0")):
        _add(db_session, _hh.id, amt, desc)
    r = Report()
    check_no_misparsed_rows(db_session, _hh.id, r)
    assert not r.errors, f"fired on healthy rows: {[f.detail for f in r.errors]}"
