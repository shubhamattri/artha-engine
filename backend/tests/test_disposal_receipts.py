"""Money arriving against a sale receivable — the one action that moves BOTH sides.

Until this existed the two halves of a settled receivable lived in different screens: the amount
still owed sat on the disposal, and the cash that actually landed sat on an asset row. Reducing the
first without raising the second deletes real money from net worth, silently. It happened for
Rs 4,00,000, and an earlier version of the same class of error cost Rs 6,50,000.

The load-bearing test in this file is test_net_worth_is_unchanged_when_money_arrives. Everything
else guards the edges around it.
"""

from datetime import date

import pytest

from api.networth import compute_assets, compute_networth
from models.asset import Asset
from models.disposal import AssetDisposal, DisposalReceipt
from models.tenant import Household
from services.disposals import outstanding_receivables, record_disposal, record_receipt

RECEIVABLE = 650_000.0
ARRIVED = 400_000.0


@pytest.fixture()
def household(db_session):
    h = Household(name="Test Household")
    db_session.add(h)
    db_session.commit()
    return h


@pytest.fixture()
def client(client_for, household):
    return client_for(household)


def _asset(db, household_id, name, asset_type="property", quantity=None, value=2_000_000.0):
    a = Asset(household_id=household_id, name=name, asset_type=asset_type, holder="Member A",
              quantity=quantity, manual_value_inr=value, active=True)
    db.add(a)
    db.commit()
    return a


def _sell(db, household_id, asset, receivable=RECEIVABLE, **kw):
    d = record_disposal(db, household_id=household_id, asset_id=asset.id,
                        disposed_on=date(2026, 7, 1), gross_proceeds_inr=8_900_000.0,
                        receivable_inr=receivable, **kw)
    db.commit()
    return d


def _cash_rows(db, household_id):
    return db.query(Asset).filter(Asset.household_id == household_id,
                                  Asset.asset_type == "cash", Asset.active.is_(True)).all()


def _receive(db, household_id, disposal, amount=ARRIVED, on=date(2026, 8, 11), notes=None):
    r = record_receipt(db, household_id=household_id, disposal_id=disposal.id,
                       received_on=on, amount_inr=amount, notes=notes)
    db.commit()
    return r


# ---------- the point of the whole feature ----------

def test_net_worth_is_unchanged_when_money_arrives(db_session, household):
    """Money moving from the receivable bucket to the cash bucket is not income. If this ever fails,
    one of the two sides moved without the other and the household's net worth is a lie."""
    _asset(db_session, household.id, "Emergency fund", asset_type="cash", value=500_000.0)
    sold = _asset(db_session, household.id, "Flat")
    d = _sell(db_session, household.id, sold)

    before = compute_networth(db_session, household.id)["net_worth"]
    _receive(db_session, household.id, d)
    after = compute_networth(db_session, household.id)["net_worth"]

    assert after == before


def test_both_sides_move_by_the_same_amount(db_session, household):
    sold = _asset(db_session, household.id, "Flat")
    d = _sell(db_session, household.id, sold)

    _receive(db_session, household.id, d)

    db_session.refresh(d)
    assert d.receivable_inr == RECEIVABLE - ARRIVED
    assert sum(r["value_inr"] for r in outstanding_receivables(db_session, household.id)) \
        == RECEIVABLE - ARRIVED
    assert sum(a.manual_value_inr for a in _cash_rows(db_session, household.id)) == ARRIVED


# ---------- the receiving cash row ----------

def test_the_cash_row_is_created_on_the_first_receipt_named_after_the_asset(db_session, household):
    """Same convention as the synthetic receivable line, which is named "<asset> — proceeds
    receivable". A second naming scheme would leave the money findable only by whoever invented it."""
    sold = _asset(db_session, household.id, "Flat")
    d = _sell(db_session, household.id, sold)

    _receive(db_session, household.id, d)

    rows = _cash_rows(db_session, household.id)
    assert [(r.name, r.manual_value_inr, r.holder) for r in rows] \
        == [("Flat — sale proceeds", ARRIVED, "Member A")]


def test_a_second_receipt_tops_up_the_same_row_rather_than_making_another(db_session, household):
    sold = _asset(db_session, household.id, "Flat")
    d = _sell(db_session, household.id, sold)

    _receive(db_session, household.id, d, amount=100_000.0)
    _receive(db_session, household.id, d, amount=250_000.0, on=date(2026, 8, 12))

    rows = _cash_rows(db_session, household.id)
    assert [(r.name, r.manual_value_inr) for r in rows] == [("Flat — sale proceeds", 350_000.0)]


def test_another_households_cash_row_is_never_credited(db_session, household):
    other = Household(name="Other")
    db_session.add(other)
    db_session.commit()
    _asset(db_session, other.id, "Flat — sale proceeds", asset_type="cash", value=0.0)
    sold = _asset(db_session, household.id, "Flat")
    d = _sell(db_session, household.id, sold)

    _receive(db_session, household.id, d)

    assert [a.manual_value_inr for a in _cash_rows(db_session, other.id)] == [0.0]
    assert [a.manual_value_inr for a in _cash_rows(db_session, household.id)] == [ARRIVED]


# ---------- the amount ----------

def test_receiving_more_than_is_outstanding_is_refused_not_clamped(db_session, household):
    sold = _asset(db_session, household.id, "Flat")
    d = _sell(db_session, household.id, sold)

    with pytest.raises(ValueError) as exc:
        record_receipt(db_session, household_id=household.id, disposal_id=d.id,
                       received_on=date(2026, 8, 11), amount_inr=RECEIVABLE + 1.0)

    assert "outstanding" in str(exc.value)
    db_session.rollback()
    assert d.receivable_inr == RECEIVABLE
    assert _cash_rows(db_session, household.id) == []


def test_receiving_the_exact_remainder_settles_it_to_zero(db_session, household):
    sold = _asset(db_session, household.id, "Flat")
    d = _sell(db_session, household.id, sold)

    _receive(db_session, household.id, d, amount=RECEIVABLE)

    db_session.refresh(d)
    assert d.receivable_inr == 0.0
    # A settled receivable stops being an asset line entirely — it is now ordinary cash.
    assert outstanding_receivables(db_session, household.id) == []
    assert compute_assets(db_session, household.id)["by_type"]["cash"] == RECEIVABLE


@pytest.mark.parametrize("amount", [0.0, -1.0])
def test_a_non_positive_amount_is_refused(db_session, household, amount):
    sold = _asset(db_session, household.id, "Flat")
    d = _sell(db_session, household.id, sold)

    with pytest.raises(ValueError) as exc:
        record_receipt(db_session, household_id=household.id, disposal_id=d.id,
                       received_on=date(2026, 8, 11), amount_inr=amount)

    assert "more than zero" in str(exc.value)


def test_a_sale_with_nothing_outstanding_cannot_receive_anything(db_session, household):
    sold = _asset(db_session, household.id, "Flat")
    d = _sell(db_session, household.id, sold, receivable=0.0)

    with pytest.raises(ValueError) as exc:
        record_receipt(db_session, household_id=household.id, disposal_id=d.id,
                       received_on=date(2026, 8, 11), amount_inr=1.0)

    assert "outstanding" in str(exc.value)


# ---------- atomicity ----------

def test_nothing_is_persisted_until_the_caller_commits(db_session, household):
    """The service writes both sides into ONE uncommitted transaction. A crash anywhere before the
    single commit therefore leaves neither side applied — that is what makes a partial unreachable."""
    sold = _asset(db_session, household.id, "Flat")
    d = _sell(db_session, household.id, sold)

    record_receipt(db_session, household_id=household.id, disposal_id=d.id,
                   received_on=date(2026, 8, 11), amount_inr=ARRIVED)
    db_session.rollback()

    db_session.refresh(d)
    assert d.receivable_inr == RECEIVABLE
    assert _cash_rows(db_session, household.id) == []
    assert db_session.query(DisposalReceipt).count() == 0


def test_a_failure_crediting_the_cash_leaves_the_receivable_untouched(db_session, household,
                                                                     monkeypatch):
    import services.disposals as mod

    sold = _asset(db_session, household.id, "Flat")
    d = _sell(db_session, household.id, sold)
    monkeypatch.setattr(mod, "_credit_proceeds_row",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(RuntimeError):
        record_receipt(db_session, household_id=household.id, disposal_id=d.id,
                       received_on=date(2026, 8, 11), amount_inr=ARRIVED)
    db_session.rollback()

    db_session.refresh(d)
    assert d.receivable_inr == RECEIVABLE
    assert db_session.query(DisposalReceipt).count() == 0


# ---------- audit trail ----------

def test_the_receipt_records_what_arrived_when_and_against_which_sale(db_session, household):
    sold = _asset(db_session, household.id, "Flat")
    d = _sell(db_session, household.id, sold)

    r = _receive(db_session, household.id, d, on=date(2026, 8, 11), notes="registration done")

    stored = db_session.query(DisposalReceipt).one()
    assert (stored.disposal_id, stored.amount_inr, stored.received_on, stored.notes) \
        == (d.id, ARRIVED, date(2026, 8, 11), "registration done")
    assert stored.household_id == household.id and stored.id == r.id


# ---------- the endpoint ----------

def test_endpoint_moves_both_sides_in_one_call(client, db_session, household):
    sold = _asset(db_session, household.id, "Flat")
    d = _sell(db_session, household.id, sold)

    r = client.post(f"/api/disposals/{d.id}/receipts",
                    json={"received_on": "2026-08-11", "amount_inr": ARRIVED})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["receivable_inr"] == RECEIVABLE - ARRIVED
    assert body["received_inr"] == ARRIVED
    assert [x["amount_inr"] for x in body["receipts"]] == [ARRIVED]
    assert body["credited_asset_id"] is not None
    db_session.expire_all()
    assert sum(a.manual_value_inr for a in _cash_rows(db_session, household.id)) == ARRIVED


def test_endpoint_refuses_more_than_outstanding_with_a_message(client, db_session, household):
    sold = _asset(db_session, household.id, "Flat")
    d = _sell(db_session, household.id, sold)

    r = client.post(f"/api/disposals/{d.id}/receipts",
                    json={"received_on": "2026-08-11", "amount_inr": RECEIVABLE + 1.0})

    assert r.status_code == 400
    assert "outstanding" in r.json()["detail"]
    db_session.expire_all()
    db_session.refresh(d)
    assert d.receivable_inr == RECEIVABLE
    assert _cash_rows(db_session, household.id) == []


def test_endpoint_rejects_a_receipt_dated_in_the_future(client, db_session, household):
    from datetime import timedelta

    sold = _asset(db_session, household.id, "Flat")
    d = _sell(db_session, household.id, sold)

    r = client.post(f"/api/disposals/{d.id}/receipts",
                    json={"received_on": (date.today() + timedelta(days=1)).isoformat(),
                          "amount_inr": ARRIVED})

    assert r.status_code == 400 and "future" in r.json()["detail"]


def test_endpoint_is_scoped_to_the_household(client, client_for, db_session, household):
    sold = _asset(db_session, household.id, "Flat")
    d = _sell(db_session, household.id, sold)
    other = Household(name="Other")
    db_session.add(other)
    db_session.commit()

    r = client_for(other).post(f"/api/disposals/{d.id}/receipts",
                               json={"received_on": "2026-08-11", "amount_inr": ARRIVED})

    assert r.status_code == 404


def test_the_list_endpoint_carries_the_receipts(client, db_session, household):
    sold = _asset(db_session, household.id, "Flat")
    d = _sell(db_session, household.id, sold)
    client.post(f"/api/disposals/{d.id}/receipts",
                json={"received_on": "2026-08-11", "amount_inr": ARRIVED})

    row = client.get("/api/disposals").json()["disposals"][0]

    assert row["received_inr"] == ARRIVED
    assert [(x["received_on"], x["amount_inr"]) for x in row["receipts"]] \
        == [("2026-08-11", ARRIVED)]


# ---------- undoing a mis-entered receipt ----------

def test_deleting_a_receipt_puts_the_money_back_on_both_sides(client, db_session, household):
    sold = _asset(db_session, household.id, "Flat")
    d = _sell(db_session, household.id, sold)
    rid = client.post(f"/api/disposals/{d.id}/receipts",
                      json={"received_on": "2026-08-11", "amount_inr": ARRIVED}
                      ).json()["receipts"][0]["id"]

    r = client.delete(f"/api/disposals/{d.id}/receipts/{rid}")

    assert r.status_code == 200, r.text
    assert r.json()["receivable_inr"] == RECEIVABLE and r.json()["receipts"] == []
    db_session.expire_all()
    assert sum(a.manual_value_inr for a in _cash_rows(db_session, household.id)) == 0.0


def test_deleting_a_receipt_leaves_net_worth_unchanged(client, db_session, household):
    sold = _asset(db_session, household.id, "Flat")
    d = _sell(db_session, household.id, sold)
    rid = client.post(f"/api/disposals/{d.id}/receipts",
                      json={"received_on": "2026-08-11", "amount_inr": ARRIVED}
                      ).json()["receipts"][0]["id"]
    before = compute_networth(db_session, household.id)["net_worth"]

    client.delete(f"/api/disposals/{d.id}/receipts/{rid}")

    db_session.expire_all()
    assert compute_networth(db_session, household.id)["net_worth"] == before


def test_a_sale_with_money_received_cannot_be_deleted_outright(client, db_session, household):
    sold = _asset(db_session, household.id, "Flat")
    d = _sell(db_session, household.id, sold)
    client.post(f"/api/disposals/{d.id}/receipts",
                json={"received_on": "2026-08-11", "amount_inr": ARRIVED})

    r = client.delete(f"/api/disposals/{d.id}")

    assert r.status_code == 400 and "received" in r.json()["detail"]
    db_session.expire_all()
    assert db_session.query(AssetDisposal).count() == 1


# ---------- the invariant ----------

def test_consistency_flags_a_receipt_whose_cash_never_landed(db_session, household):
    """The exact failure this feature exists to make unreachable: the receivable went down and
    nothing on the balance sheet went up."""
    from services.consistency import CHECKS, Report, check_disposal_receipts_have_both_sides

    assert check_disposal_receipts_have_both_sides in CHECKS
    sold = _asset(db_session, household.id, "Flat")
    d = _sell(db_session, household.id, sold)
    _receive(db_session, household.id, d)
    for a in _cash_rows(db_session, household.id):
        db_session.delete(a)
    db_session.commit()

    r = Report()
    check_disposal_receipts_have_both_sides(db_session, household.id, r)

    f = next(x for x in r.findings if x.check == "disposal_receipts_both_sides")
    assert f.severity == "error" and "Flat — sale proceeds" in f.detail


def test_consistency_passes_once_both_sides_moved(db_session, household):
    from services.consistency import Report, check_disposal_receipts_have_both_sides

    sold = _asset(db_session, household.id, "Flat")
    d = _sell(db_session, household.id, sold)
    _receive(db_session, household.id, d)

    r = Report()
    check_disposal_receipts_have_both_sides(db_session, household.id, r)

    assert next(x for x in r.findings
                if x.check == "disposal_receipts_both_sides").severity == "ok"


def test_consistency_flags_a_receivable_driven_negative(db_session, household):
    from services.consistency import Report, check_disposal_receipts_have_both_sides

    sold = _asset(db_session, household.id, "Flat")
    d = _sell(db_session, household.id, sold)
    d.receivable_inr = -5_000.0     # past the rounding tolerance, so it is a real defect
    db_session.commit()

    r = Report()
    check_disposal_receipts_have_both_sides(db_session, household.id, r)

    f = next(x for x in r.findings if x.check == "disposal_receipts_both_sides")
    assert f.severity == "error" and "negative" in f.detail
