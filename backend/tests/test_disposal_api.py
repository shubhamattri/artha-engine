"""The disposal endpoints — the thing that was missing when two real sales had to be inserted by
hand, with invented dates, because no UI could record them.

The validation tests exist because a bad disposal is silently expensive: a sale dated in the future
or bought-after-sold produces a holding period, and the holding period sets the tax rate. The PATCH
tests exist because those two hand-inserted rows carry placeholder dates that must be fixable.
"""

from datetime import date, timedelta

import pytest

from models.asset import Asset
from models.tenant import Household

GROSS = 8_000_000.0
BASIS = 5_500_000.0
COSTS = 100_000.0
LOAN = 5_000_000.0


@pytest.fixture()
def household(db_session):
    h = Household(name="Test Household")
    db_session.add(h)
    db_session.commit()
    return h


@pytest.fixture()
def client(client_for, household):
    return client_for(household)


def _asset(db, household_id, name="Flat", asset_type="property", quantity=None, value=2_000_000.0):
    a = Asset(household_id=household_id, name=name, asset_type=asset_type, holder="Member A",
              quantity=quantity, manual_value_inr=value, active=True)
    db.add(a)
    db.commit()
    return a


def _sale(**over):
    body = {"disposed_on": "2026-07-01", "acquired_on": "2023-01-01",
            "gross_proceeds_inr": GROSS, "costs_inr": COSTS, "cost_basis_inr": BASIS}
    body.update(over)
    return body


# ---------- create ----------

def test_dispose_records_the_sale_and_closes_the_asset(client, db_session, household):
    a = _asset(db_session, household.id)

    r = client.post(f"/api/assets/{a.id}/dispose", json=_sale(loan_settled_inr=LOAN,
                                                              receivable_inr=1_000_000.0))

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["gross_proceeds_inr"] == GROSS
    # Net received subtracts the loan; the gain (below) does not.
    assert body["net_received_inr"] == GROSS - COSTS - LOAN - 1_000_000.0
    db_session.refresh(a)
    assert a.active is False


def test_part_sale_leaves_the_asset_holding_the_remainder(client, db_session, household):
    a = _asset(db_session, household.id, name="Units", asset_type="other", quantity=100.0)

    r = client.post(f"/api/assets/{a.id}/dispose", json=_sale(quantity=40.0))

    assert r.status_code == 200, r.text
    db_session.refresh(a)
    assert a.active is True and a.quantity == 60.0


def test_another_households_asset_cannot_be_disposed(client, db_session, household):
    other = Household(name="Other")
    db_session.add(other)
    db_session.commit()
    a = _asset(db_session, other.id)

    assert client.post(f"/api/assets/{a.id}/dispose", json=_sale()).status_code == 404


# ---------- validation ----------

@pytest.mark.parametrize("over, fragment", [
    ({"gross_proceeds_inr": 0}, "more than zero"),
    ({"gross_proceeds_inr": -1.0}, "more than zero"),
    ({"disposed_on": (date.today() + timedelta(days=1)).isoformat()}, "future"),
    ({"acquired_on": "2026-07-02"}, "Bought after it was sold"),
])
def test_bad_sale_is_rejected(client, db_session, household, over, fragment):
    a = _asset(db_session, household.id)

    r = client.post(f"/api/assets/{a.id}/dispose", json=_sale(**over))

    assert r.status_code == 400
    assert fragment in r.json()["detail"]
    db_session.refresh(a)
    assert a.active is True  # a rejected sale must not have touched the asset


def test_cannot_sell_more_than_the_asset_holds(client, db_session, household):
    a = _asset(db_session, household.id, name="Units", asset_type="other", quantity=100.0)

    r = client.post(f"/api/assets/{a.id}/dispose", json=_sale(quantity=140.0))

    assert r.status_code == 400 and "holds 100.0" in r.json()["detail"]
    db_session.refresh(a)
    assert a.quantity == 100.0


def test_zero_quantity_is_rejected_rather_than_read_as_whole_asset(client, db_session, household):
    a = _asset(db_session, household.id, name="Units", asset_type="other", quantity=100.0)

    r = client.post(f"/api/assets/{a.id}/dispose", json=_sale(quantity=0))

    assert r.status_code == 400 and "more than zero" in r.json()["detail"]


def test_disposed_today_is_allowed(client, db_session, household):
    a = _asset(db_session, household.id)

    r = client.post(f"/api/assets/{a.id}/dispose",
                    json=_sale(disposed_on=date.today().isoformat()))

    assert r.status_code == 200, r.text


# ---------- list ----------

def test_list_reports_the_gain_ignoring_the_loan_settled(client, db_session, household):
    a = _asset(db_session, household.id)
    client.post(f"/api/assets/{a.id}/dispose", json=_sale(loan_settled_inr=LOAN))

    row = client.get("/api/disposals").json()["disposals"][0]

    # Repaying a loan out of the proceeds reduces the gain by NOTHING; costs do reduce it.
    assert row["gain_inr"] == GROSS - COSTS - BASIS
    assert row["loan_settled_inr"] == LOAN
    assert row["is_long_term"] is True
    assert row["tax_inr"] == pytest.approx((GROSS - COSTS - BASIS) * 0.125 * 1.04)
    assert row["id"] is not None and row["asset_id"] == a.id


def test_unknown_cost_basis_gives_no_gain_rather_than_zero(client, db_session, household):
    a = _asset(db_session, household.id, name="ESOP shares", asset_type="stock", quantity=10.0)
    client.post(f"/api/assets/{a.id}/dispose", json=_sale(cost_basis_inr=None))

    body = client.get("/api/disposals").json()

    row = body["disposals"][0]
    assert row["gain_inr"] is None and row["tax_inr"] is None
    assert row["method"] == "unknown" and "FMV" in row["why"]
    assert body["unpriced_count"] == 1


def test_list_is_scoped_to_the_household(client, client_for, db_session, household):
    a = _asset(db_session, household.id)
    client.post(f"/api/assets/{a.id}/dispose", json=_sale())

    other = Household(name="Other")
    db_session.add(other)
    db_session.commit()

    assert client_for(other).get("/api/disposals").json()["disposals"] == []


def test_same_day_sales_keep_their_own_ids(client, db_session, household):
    a = _asset(db_session, household.id, name="A")
    b = _asset(db_session, household.id, name="B")
    client.post(f"/api/assets/{a.id}/dispose", json=_sale(gross_proceeds_inr=1_000_000.0))
    client.post(f"/api/assets/{b.id}/dispose", json=_sale(gross_proceeds_inr=2_000_000.0))

    rows = client.get("/api/disposals").json()["disposals"]

    by_asset = {r["asset_id"]: r for r in rows}
    assert by_asset[a.id]["gross_proceeds_inr"] == 1_000_000.0
    assert by_asset[b.id]["gross_proceeds_inr"] == 2_000_000.0
    assert len({r["id"] for r in rows}) == 2


# ---------- correct (PATCH) ----------

def test_patch_fixes_a_placeholder_date_and_the_gain_follows(client, db_session, household):
    a = _asset(db_session, household.id)
    # A placeholder acquisition date that makes a long-held flat look short-term.
    did = client.post(f"/api/assets/{a.id}/dispose",
                      json=_sale(acquired_on="2026-01-01")).json()["id"]
    assert client.get("/api/disposals").json()["disposals"][0]["is_long_term"] is False

    r = client.patch(f"/api/disposals/{did}", json={"acquired_on": "2020-01-01"})

    assert r.status_code == 200, r.text
    assert r.json()["acquired_on"] == "2020-01-01"
    row = client.get("/api/disposals").json()["disposals"][0]
    assert row["is_long_term"] is True and row["months_held"] == 78


def test_patch_only_touches_the_fields_it_is_given(client, db_session, household):
    a = _asset(db_session, household.id)
    did = client.post(f"/api/assets/{a.id}/dispose",
                      json=_sale(loan_settled_inr=LOAN, notes="registration pending")).json()["id"]

    body = client.patch(f"/api/disposals/{did}", json={"receivable_inr": 250_000.0}).json()

    assert body["receivable_inr"] == 250_000.0
    assert body["loan_settled_inr"] == LOAN and body["cost_basis_inr"] == BASIS
    assert body["notes"] == "registration pending"


def test_patch_can_clear_a_guessed_cost_basis(client, db_session, household):
    a = _asset(db_session, household.id)
    did = client.post(f"/api/assets/{a.id}/dispose", json=_sale()).json()["id"]

    assert client.patch(f"/api/disposals/{did}",
                        json={"cost_basis_inr": None}).json()["cost_basis_inr"] is None
    assert client.get("/api/disposals").json()["disposals"][0]["gain_inr"] is None


@pytest.mark.parametrize("patch, fragment", [
    ({"gross_proceeds_inr": 0}, "more than zero"),
    ({"disposed_on": (date.today() + timedelta(days=365)).isoformat()}, "future"),
    ({"acquired_on": "2026-08-01"}, "Bought after it was sold"),
])
def test_patch_is_validated_like_the_original_entry(client, db_session, household, patch, fragment):
    a = _asset(db_session, household.id)
    did = client.post(f"/api/assets/{a.id}/dispose", json=_sale()).json()["id"]

    r = client.patch(f"/api/disposals/{did}", json=patch)

    assert r.status_code == 400 and fragment in r.json()["detail"]
    assert client.get("/api/disposals").json()["disposals"][0]["gross_proceeds_inr"] == GROSS


def test_patch_quantity_is_judged_against_the_pre_sale_holding(client, db_session, household):
    a = _asset(db_session, household.id, name="Units", asset_type="other", quantity=100.0)
    did = client.post(f"/api/assets/{a.id}/dispose", json=_sale(quantity=40.0)).json()["id"]

    # 70 of the ORIGINAL 100 is valid even though only 60 are left showing after the first record.
    assert client.patch(f"/api/disposals/{did}", json={"quantity": 70.0}).status_code == 200
    db_session.refresh(a)
    assert a.quantity == 30.0 and a.active is True

    r = client.patch(f"/api/disposals/{did}", json={"quantity": 140.0})
    assert r.status_code == 400 and "holds 100.0" in r.json()["detail"]
    db_session.refresh(a)
    assert a.quantity == 30.0  # the rejected correction changed nothing


def test_patch_to_the_whole_holding_closes_the_asset(client, db_session, household):
    a = _asset(db_session, household.id, name="Units", asset_type="other", quantity=100.0)
    did = client.post(f"/api/assets/{a.id}/dispose", json=_sale(quantity=40.0)).json()["id"]

    client.patch(f"/api/disposals/{did}", json={"quantity": 100.0})

    db_session.refresh(a)
    assert a.active is False


def test_patching_another_households_disposal_is_a_404(client, client_for, db_session, household):
    a = _asset(db_session, household.id)
    did = client.post(f"/api/assets/{a.id}/dispose", json=_sale()).json()["id"]

    other = Household(name="Other")
    db_session.add(other)
    db_session.commit()

    assert client_for(other).patch(f"/api/disposals/{did}",
                                   json={"notes": "x"}).status_code == 404


# ---------- delete ----------

def test_deleting_a_sale_gives_the_asset_back(client, db_session, household):
    a = _asset(db_session, household.id)
    did = client.post(f"/api/assets/{a.id}/dispose", json=_sale()).json()["id"]
    db_session.refresh(a)
    assert a.active is False

    assert client.delete(f"/api/disposals/{did}").status_code == 200

    db_session.refresh(a)
    assert a.active is True
    assert client.get("/api/disposals").json()["disposals"] == []


def test_deleting_a_part_sale_restores_the_quantity(client, db_session, household):
    a = _asset(db_session, household.id, name="Units", asset_type="other", quantity=100.0)
    did = client.post(f"/api/assets/{a.id}/dispose", json=_sale(quantity=40.0)).json()["id"]

    client.delete(f"/api/disposals/{did}")

    db_session.refresh(a)
    assert a.quantity == 100.0 and a.active is True
