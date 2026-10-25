"""Money that has not arrived cannot be deployed.

`_investable_cash` took `by_type["cash"]` from api/networth, and that figure deliberately includes
outstanding sale receivables, because a receivable IS an asset you own. For NET WORTH that is right.
For "how much can I put to work today" it is wrong, and the disposal model says so in its own words:
"proceeds not yet received (registration pending). Real, owed, not yet spendable."

A property sale can leave part of its proceeds owed until registration. The calculator offered it as
deployable, so the default amount was overstated by exactly that. Small here, and not small the next
time a sale completes with a large tail outstanding.

Net worth is untouched on purpose. Only the spendable figure changes.
"""

from datetime import date

from models.asset import Asset
from models.disposal import AssetDisposal
from models.tenant import Household


def _household(db_session) -> Household:
    # asset_disposals is not in conftest's table allowlist; same pattern as test_disposal_api.
    AssetDisposal.__table__.create(db_session.get_bind(), checkfirst=True)
    h = Household(name="RECV")
    db_session.add(h)
    db_session.commit()
    return h


def _cash(db_session, hid: int, name: str, amount: float) -> Asset:
    a = Asset(household_id=hid, name=name, asset_type="cash", currency="INR",
              manual_value_inr=amount, active=True)
    db_session.add(a)
    db_session.commit()
    return a


def _sold_with_receivable(db_session, hid: int, asset: Asset, receivable: float) -> AssetDisposal:
    d = AssetDisposal(household_id=hid, asset_id=asset.id, disposed_on=date(2026, 6, 1),
                      gross_proceeds_inr=1_000_000.0, receivable_inr=receivable,
                      cost_basis_inr=800_000.0)
    db_session.add(d)
    db_session.commit()
    return d


def test_a_receivable_is_not_offered_as_deployable(db_session):
    """The bug, stated as a test."""
    from api.windfall import _investable_cash
    h = _household(db_session)
    _cash(db_session, h.id, "Bank", 1_000_000.0)
    sold = _cash(db_session, h.id, "Flat (sold)", 0.0)
    _sold_with_receivable(db_session, h.id, sold, 300_000.0)

    out = _investable_cash(db_session, h.id)
    assert out["receivable_inr"] == 300_000.0
    assert out["liquid_cash_inr"] == 1_000_000.0, "the receivable is not spendable cash"


def test_net_worth_still_counts_the_receivable(db_session):
    """The other half of the rule. Excluding it from net worth would delete money he is genuinely owed."""
    from api.networth import compute_assets
    h = _household(db_session)
    _cash(db_session, h.id, "Bank", 1_000_000.0)
    sold = _cash(db_session, h.id, "Flat (sold)", 0.0)
    _sold_with_receivable(db_session, h.id, sold, 300_000.0)

    assert compute_assets(db_session, h.id)["by_type"]["cash"] == 1_300_000.0


def test_with_no_receivable_the_figure_is_unchanged(db_session):
    """Every household without a pending sale must see exactly what it saw before."""
    from api.windfall import _investable_cash
    h = _household(db_session)
    _cash(db_session, h.id, "Bank", 1_000_000.0)

    out = _investable_cash(db_session, h.id)
    assert out["receivable_inr"] == 0.0
    assert out["liquid_cash_inr"] == 1_000_000.0


def test_a_receivable_far_larger_than_the_bank_leaves_only_the_bank(db_session):
    """A sale whose proceeds are almost entirely pending must leave the real bank balance and nothing
    more. Net worth reads 9.5L here; only 50k of it has arrived."""
    from api.networth import compute_assets
    from api.windfall import _investable_cash
    h = _household(db_session)
    _cash(db_session, h.id, "Bank", 50_000.0)
    sold = _cash(db_session, h.id, "Flat (sold)", 0.0)
    _sold_with_receivable(db_session, h.id, sold, 900_000.0)

    assert compute_assets(db_session, h.id)["by_type"]["cash"] == 950_000.0
    out = _investable_cash(db_session, h.id)
    assert out["receivable_inr"] == 900_000.0
    assert out["liquid_cash_inr"] == 50_000.0
    assert out["amount_inr"] >= 0.0, "deployable cash can never go negative"
