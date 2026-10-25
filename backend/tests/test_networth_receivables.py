"""Pins disposal receivables into BOTH of net worth's asset queries.

api/networth.py runs two independent asset queries — compute_assets() and compute_networth(). The
receivable line was added to the first only, so the two endpoints disagreed by the receivable amount
through a whole deploy without a single failure. These tests fail if either query drops it again.
"""

from datetime import date

import pytest

from api.networth import compute_assets, compute_networth
from models.asset import Asset
from models.tenant import Household
from services.disposals import record_disposal

RECEIVABLE = 650_000.0
SOLD_VALUE = 2_000_000.0


@pytest.fixture()
def household(db_session):
    h = Household(name="Test Household")
    db_session.add(h)
    db_session.commit()
    return h


def _asset(db, household_id, name, asset_type="property", quantity=None, value=SOLD_VALUE):
    """Manual-valued, no ticker and no meta, so nothing here reaches a live price feed."""
    a = Asset(household_id=household_id, name=name, asset_type=asset_type, holder="Member A",
              quantity=quantity, manual_value_inr=value, active=True)
    db.add(a)
    db.commit()
    return a


def _sell(db, household_id, asset, **kw):
    d = record_disposal(db, household_id=household_id, asset_id=asset.id,
                        disposed_on=date(2026, 7, 1), gross_proceeds_inr=8_900_000.0, **kw)
    db.commit()
    return d


def _receivable_lines(assets):
    return [a for a in assets if a.get("disposal_id")]


def _cash_items(networth):
    cash = [c for c in networth["categories"] if c["category"] == "Cash & Bank"]
    return [i for c in cash for i in c["items"]]


def test_compute_assets_includes_the_receivable(db_session, household):
    sold = _asset(db_session, household.id, "Flat")
    _sell(db_session, household.id, sold, receivable_inr=RECEIVABLE)

    res = compute_assets(db_session, household.id)

    assert [a["value_inr"] for a in _receivable_lines(res["assets"])] == [RECEIVABLE]
    assert [a["asset_type"] for a in _receivable_lines(res["assets"])] == ["cash"]
    # The sold asset is deactivated, so the receivable is the only thing left standing.
    assert res["total_inr"] == RECEIVABLE
    assert res["by_type"]["cash"] == RECEIVABLE


def test_compute_networth_includes_the_same_receivable(db_session, household):
    sold = _asset(db_session, household.id, "Flat")
    _sell(db_session, household.id, sold, receivable_inr=RECEIVABLE)

    nw = compute_networth(db_session, household.id)

    assert [i["value"] for i in _cash_items(nw)] == [RECEIVABLE]
    assert nw["total_assets"] == RECEIVABLE
    assert nw["net_worth"] == RECEIVABLE


def test_the_two_queries_do_not_disagree_by_the_receivable(db_session, household):
    # A surviving asset alongside the sale, so a dropped receivable shows up as a gap between the
    # two totals rather than as both collapsing to zero.
    _asset(db_session, household.id, "Emergency fund", asset_type="cash", value=500_000.0)
    sold = _asset(db_session, household.id, "Flat")
    _sell(db_session, household.id, sold, receivable_inr=RECEIVABLE)

    assets_total = compute_assets(db_session, household.id)["total_inr"]
    networth_total = compute_networth(db_session, household.id)["total_assets"]

    assert assets_total == networth_total == 500_000.0 + RECEIVABLE


def test_fully_disposed_asset_is_excluded_from_both(db_session, household):
    sold = _asset(db_session, household.id, "Flat")
    _sell(db_session, household.id, sold, receivable_inr=0.0)  # whole asset, nothing owed

    res = compute_assets(db_session, household.id)
    nw = compute_networth(db_session, household.id)

    assert res["assets"] == [] and res["total_inr"] == 0.0
    assert [i["name"] for c in nw["categories"] for i in c["items"]] == []
    assert nw["total_assets"] == 0.0


def test_part_disposal_stays_active_with_reduced_quantity_in_both(db_session, household):
    held = _asset(db_session, household.id, "Units", asset_type="other",
                  quantity=100.0, value=1_000_000.0)
    _sell(db_session, household.id, held, quantity=40.0, receivable_inr=RECEIVABLE)

    assert held.active is True and held.quantity == 60.0

    res = compute_assets(db_session, household.id)
    nw = compute_networth(db_session, household.id)

    assert [(a["name"], a["quantity"]) for a in res["assets"] if a["name"] == "Units"] \
        == [("Units", 60.0)]
    assert [a["value_inr"] for a in _receivable_lines(res["assets"])] == [RECEIVABLE]
    assert [i["value"] for i in _cash_items(nw)] == [RECEIVABLE]
    assert res["total_inr"] == nw["total_assets"] == 1_000_000.0 + RECEIVABLE
