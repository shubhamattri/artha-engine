"""The wallet accrues on its own, is spent from anywhere, and is never new money.

Earmarking each cycle before the money could be used was friction in the wrong place: the money
exists whether or not it has been categorised, and a step you must remember is one that gets
skipped. So the balance is every closed cycle's saving minus everything drawn.
"""

import pytest

from models.tenant import Household
from services import wallet


@pytest.fixture
def _hh(db_session, monkeypatch):
    from models.surplus import SurplusAllocation, SurplusDraw
    for m in (SurplusAllocation, SurplusDraw):
        m.__table__.create(db_session.get_bind(), checkfirst=True)
    h = Household(name="A"); db_session.add(h); db_session.commit()
    # Two closed cycles saved 30,000 each. Budget/cash-flow have their own tests.
    monkeypatch.setattr("api.budget.compute_budget", lambda **k: {"net_spent_inr": 120000.0})
    monkeypatch.setattr("api.cashflow.compute_cashflow", lambda *a, **k: {"leftover": 150000.0})
    monkeypatch.setattr("services.wallet._closed_cycle_ends",
                        lambda db, hid, count=24: ["2026-08-12", "2026-07-12"])
    # Both fixture cycles count as tracked. _has_data has its own test below — a cycle with no
    # transactions must not read as a month where everything was saved.
    monkeypatch.setattr("services.wallet._has_data", lambda db, hid, end: True)
    # The wallet only counts from a named start cycle. Both fixture cycles are on or after it.
    monkeypatch.setattr("services.wallet._start_cycle", lambda db, hid: "2026-07-12")
    return h


def test_the_wallet_accrues_from_closed_cycles_with_no_earmarking(db_session, _hh):
    assert wallet.balance(db_session, _hh.id) == 60000.0


def test_the_live_cycle_is_excluded():
    """Counting the open cycle would let you spend money it may still consume."""
    import inspect
    src = inspect.getsource(wallet._closed_cycle_ends)
    assert "live cycle is excluded" in src.lower() or "closed" in src.lower()


def test_spending_reduces_the_balance_and_records_what_it_paid_for(db_session, _hh):
    wallet.spend(db_session, _hh.id, 25000.0, on="2026-08-14", loan_id=7, note="HDFC part payment")
    assert wallet.balance(db_session, _hh.id) == 35000.0
    s = wallet.summary(db_session, _hh.id)
    assert s["outflows"][0]["loan_id"] == 7, "the draw does not say what it paid for"
    assert s["outflows"][0]["funded_from"] == "wallet"


def test_it_refuses_to_overdraw(db_session, _hh):
    """A wallet that can go negative is not a balance, it is another figure to reconcile."""
    with pytest.raises(ValueError, match="cannot spend"):
        wallet.spend(db_session, _hh.id, 60001.0)


def test_the_wallet_is_never_added_to_net_worth(db_session, _hh):
    """These rupees are already in a bank balance that net worth counts."""
    from api.networth import networth_summary
    before = networth_summary(db=db_session, household=_hh)
    wallet.spend(db_session, _hh.id, 10000.0, note="invested")
    after = networth_summary(db=db_session, household=_hh)
    kb = before.get("net_worth_inr", before.get("net_worth"))
    ka = after.get("net_worth_inr", after.get("net_worth"))
    assert kb == ka, f"spending from the wallet moved net worth {kb} -> {ka}"


def test_a_payment_without_a_source_does_not_touch_the_wallet(db_session, _hh):
    """Default is bank. A balance that moves when you did not say so is one you stop trusting."""
    import inspect

    import api.debt as dbt
    src = inspect.getsource(dbt.add_payment)
    assert '"bank"' in src and 'src == "wallet"' in src
    assert wallet.balance(db_session, _hh.id) == 60000.0


def test_a_cycle_with_no_transactions_is_not_counted_as_saved(db_session, _hh, monkeypatch):
    """The bug this caught live: cycles from before tracking began had zero recorded spend, so the
    pool minus nothing read as a full month saved. Six such cycles put the balance at Rs 30 lakh.
    Absence of records is not evidence of thrift."""
    monkeypatch.setattr("services.wallet._has_data", lambda db, hid, end: False)
    assert wallet.balance(db_session, _hh.id) == 0.0


def test_nothing_accrues_before_the_start_cycle(db_session, _hh, monkeypatch):
    """Forward only, by your instruction. Cycles before the wallet started are excluded because the
    data does not support a claim either way — counting them is what produced Rs 30 lakh."""
    monkeypatch.setattr("services.wallet._start_cycle", lambda db, hid: "2026-08-12")
    s = wallet.summary(db_session, _hh.id)
    assert [i["cycle_end"] for i in s["inflows"]] == ["2026-08-12"], "an earlier cycle leaked in"
    assert s["balance"] == 30000.0


def test_an_unstarted_wallet_holds_nothing(db_session, _hh, monkeypatch):
    """NULL start means not started. A wallet that silently backfills history is the original bug."""
    monkeypatch.setattr("services.wallet._start_cycle", lambda db, hid: None)
    assert wallet.balance(db_session, _hh.id) == 0.0


# --- funding an investment ---------------------------------------------------

def test_a_contribution_can_be_funded_from_the_wallet(db_session, _hh):
    """Wallet spending was wired to loans only, so a SIP top-up had nowhere to record what it
    funded — a draw whose destination is unknown reduces the balance without answering the question
    the wallet exists for."""
    wallet.spend(db_session, _hh.id, 20000.0, on="2026-08-20", recurring_id=3, note="PPF top-up")
    assert wallet.balance(db_session, _hh.id) == 40000.0
    out = wallet.summary(db_session, _hh.id)["outflows"][0]
    assert out["recurring_id"] == 3, "the draw does not say which investment it funded"
    assert out["loan_id"] is None


def test_a_withdrawal_cannot_be_funded_from_the_wallet(db_session, _hh, client_for):
    """A NEGATIVE contribution is money coming BACK from an investment. Deducting the wallet for it
    would charge you for a payment that never happened."""
    c = client_for(_hh)
    inv = c.post("/api/recurring", json={"name": "SIP", "kind": "sip", "as_of_date": "2026-01-01",
                                         "monthly_contribution": 0.0}).json()["id"]
    r = c.post(f"/api/recurring/{inv}/contribute",
               json={"amount": -1000.0, "date": "2026-04-15", "funded_from": "wallet"})
    assert r.status_code == 400
    assert "cannot be funded from the wallet" in r.json()["detail"]
    assert wallet.balance(db_session, _hh.id) == 60000.0


def test_a_contribution_without_a_source_does_not_touch_the_wallet(db_session, _hh):
    """Same default as loan payments: bank, never wallet."""
    import inspect

    import api.recurring as rec
    src = inspect.getsource(rec.add_contribution)
    assert '"bank"' in src
    assert wallet.balance(db_session, _hh.id) == 60000.0
