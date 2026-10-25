"""Pins GET /networth/composition — the Wealth tab's breakdown BY NATURE, not by account.

The screen it replaces could only say which account held what. These tests pin the thing that is
actually hard: membership is DERIVED (a fund is market-exposed only once a NAV resolves, an EPF is
not), the five buckets account for every rupee of total assets, and the numbers come from the
modules that already own them rather than from a second query that can drift.
"""

from datetime import date, datetime, timezone

import pytest
from api.networth import compute_composition
from models.asset import Asset
from models.recurring import RecurringInvestment
from models.sinking import SinkingFund
from models.tenant import Household

PROPERTY = 7_000_000.0
STOCK = 25_000.0
GOLD = 500_000.0
CASH = 400_000.0
VEHICLE = 300_000.0
EPF = 100_000.0
FUND_UNITS, FUND_NAV = 1_000.0, 150.0      # → 1,50,000 of market-priced holding
FUND = FUND_UNITS * FUND_NAV
SINKING = 50_000.0

TOTAL = PROPERTY + STOCK + GOLD + CASH + VEHICLE + EPF + FUND + SINKING   # 85,25,000


@pytest.fixture()
def household(db_session):
    h = Household(name="Test Household")
    db_session.add(h)
    db_session.commit()
    return h


def _asset(db, hid, name, asset_type, value):
    """Manual value, no ticker and no meta, so nothing here reaches a live price feed."""
    db.add(Asset(household_id=hid, name=name, asset_type=asset_type, holder="Member A",
                 manual_value_inr=value, active=True))


def _fund(db, hid):
    """A market-priced SIP with a FRESH cached NAV — resolved without touching the network."""
    db.add(RecurringInvestment(
        household_id=hid, kind="sip", name="Index Fund", opening_balance=FUND,
        as_of_date=date.today(), monthly_contribution=0.0, annual_rate_pct=12.0,
        compounding="monthly",
        market={"scheme_code": "1", "units": FUND_UNITS, "last_nav": FUND_NAV,
                "nav_date": date.today().isoformat(),
                "last_nav_at": datetime.now(timezone.utc).isoformat()}))


@pytest.fixture()
def populated(db_session, household):
    """One holding of every nature the five buckets have to cover."""
    hid = household.id
    _asset(db_session, hid, "Flat", "property", PROPERTY)
    _asset(db_session, hid, "Shares", "stock", STOCK)
    _asset(db_session, hid, "Coins", "gold", GOLD)
    _asset(db_session, hid, "Savings", "cash", CASH)
    _asset(db_session, hid, "Car", "vehicle", VEHICLE)
    db_session.add(RecurringInvestment(
        household_id=hid, kind="epf", name="EPF", opening_balance=EPF,
        as_of_date=date.today(), monthly_contribution=0.0, annual_rate_pct=8.25,
        compounding="annual"))
    _fund(db_session, hid)
    db_session.add(SinkingFund(household_id=hid, name="Travel", opening_balance=SINKING,
                               as_of_date=date.today(), monthly_contribution=0.0, active=True))
    db_session.commit()
    return household


def _by_key(out):
    return {b["key"]: b for b in out["buckets"]}


def test_the_five_buckets_account_for_every_rupee_of_total_assets(db_session, populated):
    """The invariant the whole screen rests on. A holding in no bucket shrinks the breakdown while
    net worth stays right, so the tab and the headline disagree with nothing failing."""
    out = compute_composition(db_session, populated.id)

    assert out["total_assets"] == pytest.approx(TOTAL)
    assert round(sum(b["value_inr"] for b in out["buckets"]), 2) == pytest.approx(TOTAL)
    assert {b["key"] for b in out["buckets"]} == {
        "property", "market_exposed", "locked", "cash", "depreciating"}


def test_buckets_group_by_nature_and_come_back_biggest_first(db_session, populated):
    """Gold + stock + a priced fund are ONE thing (market-exposed) though they live in three
    different tables; a sinking fund is cash though it lives in neither asset nor recurring."""
    out = compute_composition(db_session, populated.id)
    b = _by_key(out)

    assert b["property"]["value_inr"] == pytest.approx(PROPERTY)
    assert b["market_exposed"]["value_inr"] == pytest.approx(STOCK + GOLD + FUND)
    assert b["locked"]["value_inr"] == pytest.approx(EPF)
    assert b["cash"]["value_inr"] == pytest.approx(CASH + SINKING)
    assert b["depreciating"]["value_inr"] == pytest.approx(VEHICLE)

    assert b["market_exposed"]["count"] == 3      # stock + gold + the fund
    assert b["cash"]["count"] == 2                # cash row + sinking fund
    assert b["property"]["count"] == 1

    values = [x["value_inr"] for x in out["buckets"]]
    assert values == sorted(values, reverse=True)
    assert b["property"]["pct"] == pytest.approx(round(PROPERTY / TOTAL * 100, 1))


def test_a_fund_is_market_exposed_only_once_its_nav_resolves(db_session, household):
    """The derived bit. Same SIP row, same kind — no NAV means the value is an assumed rate, which
    is the same footing as an EPF, so it must NOT be counted as live-priced market exposure."""
    _fund(db_session, household.id)
    db_session.commit()
    priced = _by_key(compute_composition(db_session, household.id))
    assert priced["market_exposed"]["value_inr"] == pytest.approx(FUND)
    assert priced["locked"]["value_inr"] == 0.0

    inv = db_session.query(RecurringInvestment).one()
    inv.market = None                              # same SIP, no scheme/units → nothing to price
    inv.opening_balance = FUND
    db_session.commit()

    unpriced = _by_key(compute_composition(db_session, household.id))
    assert unpriced["market_exposed"]["value_inr"] == 0.0
    assert unpriced["locked"]["value_inr"] == pytest.approx(FUND)


def test_estimated_pct_is_the_share_of_assets_that_is_not_live_priced(db_session, populated):
    """Property, locked and depreciating carry no feed, so their share is the part of the balance
    sheet the household is being asked to take on trust."""
    out = compute_composition(db_session, populated.id)

    assert out["estimated_pct"] == round((PROPERTY + EPF + VEHICLE) / TOTAL * 100)
    assert [b["key"] for b in out["buckets"] if not b["live_priced"]] != []
    assert {b["key"] for b in out["buckets"] if b["live_priced"]} == {"market_exposed", "cash"}


def test_a_household_with_nothing_gets_five_zero_buckets_not_an_empty_list(db_session, household):
    """The frontend renders its empty state FROM the zeroes. An empty list or a division by zero
    both break that screen instead of showing it."""
    out = compute_composition(db_session, household.id)

    assert len(out["buckets"]) == 5
    assert out["total_assets"] == 0.0
    assert out["estimated_pct"] == 0
    assert all(b["value_inr"] == 0.0 and b["pct"] == 0.0 and b["count"] == 0
               for b in out["buckets"])


def test_composition_is_scoped_to_the_household(db_session, populated):
    """Every neighbour in api/networth.py is tenant-scoped; a breakdown that leaks another
    household's flat into the property bucket is the one bug this product cannot ship."""
    other = Household(name="Someone Else")
    db_session.add(other)
    db_session.commit()
    _asset(db_session, other.id, "Their Flat", "property", 9_000_000.0)
    db_session.commit()

    mine = compute_composition(db_session, populated.id)
    assert mine["total_assets"] == pytest.approx(TOTAL)
    assert _by_key(mine)["property"]["value_inr"] == pytest.approx(PROPERTY)

    theirs = compute_composition(db_session, other.id)
    assert _by_key(theirs)["property"]["value_inr"] == pytest.approx(9_000_000.0)


def test_endpoint_returns_the_contract_the_frontend_is_built_against(db_session, populated,
                                                                    client_for):
    """The frontend is being written in parallel against these exact keys."""
    out = client_for(populated).get("/api/networth/composition").json()

    assert out["as_of"] == date.today().isoformat()
    assert set(out) == {"as_of", "net_worth", "total_assets", "total_liabilities",
                        "estimated_pct", "buckets"}
    assert out["net_worth"] == pytest.approx(TOTAL)          # no loans in this fixture
    assert set(out["buckets"][0]) == {"key", "label", "sublabel", "count", "value_inr", "pct",
                                      "live_priced"}
    assert _by_key(out)["locked"]["label"] == "Locked & accruing"


def test_invariant_passes_when_every_holding_lands_in_a_bucket(db_session, populated):
    from services.consistency import Report, check_composition_covers_total_assets

    r = Report()
    check_composition_covers_total_assets(db_session, populated.id, r)
    f = next(x for x in r.findings if x.check == "composition_covers_total_assets")
    assert f.severity == "ok"


def test_invariant_catches_assets_that_no_bucket_holds(db_session, populated):
    """What the check is FOR: a holding the bucket map does not know about would leave net worth
    correct and the breakdown quietly short."""
    from unittest.mock import patch

    from services.consistency import Report, check_composition_covers_total_assets

    real = compute_composition(db_session, populated.id)
    inflated = {**real, "total_assets": real["total_assets"] + 250_000.0}
    r = Report()
    with patch("api.networth.compute_networth", return_value=inflated):
        check_composition_covers_total_assets(db_session, populated.id, r)

    f = next(x for x in r.findings if x.check == "composition_covers_total_assets")
    assert f.severity == "error"
    assert f.gap == pytest.approx(-250_000.0)
