"""A recorded transaction history must not be counted twice.

`_view` in api/recurring builds a fund's money-in from three places: the opening balance at its
anchor, every entry in `contributions`, and a synthetic monthly flow generated from
`monthly_contribution` for each month since the anchor. That third source is right while the ledger
holds only ad-hoc entries.

Import a real history — a CAS statement, or entries typed by hand — and the recorded instalments and
the invented ones describe the SAME money. Everything downstream inflates: `total_contributed`, the
invested base a return is measured against, and the XIRR flows. Silently, which is the failure this
codebase keeps having to design against.

`ledger_is_authoritative` is how the ledger says it is complete. These tests pin both halves: that
the generator still runs when it is false, and that nothing is invented when it is true.
"""

from datetime import date, timedelta

from models.recurring import RecurringInvestment
from models.tenant import Household


def _household(db_session) -> Household:
    h = Household(name="LEDGER")
    db_session.add(h)
    db_session.commit()
    return h


def _fund(db_session, hid: int, *, authoritative: bool, contributions=None) -> RecurringInvestment:
    """A SIP anchored 6 months ago at 10,000/mo, so the generator has months to invent."""
    inv = RecurringInvestment(
        household_id=hid, name="Test Flexi Cap", kind="sip", holder="Household",
        opening_balance=0.0, as_of_date=date.today() - timedelta(days=185),
        monthly_contribution=10_000.0, employer_contribution=0.0,
        annual_rate_pct=12.0, compounding="monthly", active=True, from_takehome=True,
        contributions=contributions or [], ledger_is_authoritative=authoritative,
    )
    db_session.add(inv)
    db_session.commit()
    return inv


def _view_of(db_session, inv: RecurringInvestment) -> dict:
    from api.recurring import compute_recurring
    out = compute_recurring(db_session, inv.household_id)
    return next(i for i in out["investments"] if i["id"] == inv.id)


def test_the_monthly_flow_is_still_invented_when_the_ledger_is_not_complete(db_session):
    """Today's behaviour, pinned. A fund with an empty ledger must keep accruing from its
    instruction, or every existing household's figure would change the day this column landed."""
    h = _household(db_session)
    inv = _fund(db_session, h.id, authoritative=False)
    assert _view_of(db_session, inv)["total_contributed"] > 0


def test_nothing_is_invented_once_the_ledger_is_authoritative(db_session):
    """The bug this exists to prevent. Six months at 10,000 would be ~60,000 of invented money on
    top of the one real entry, so a wrong answer here is not subtle."""
    h = _household(db_session)
    inv = _fund(db_session, h.id, authoritative=True,
                contributions=[{"date": (date.today() - timedelta(days=100)).isoformat(),
                                "amount": 25_000.0}])
    assert _view_of(db_session, inv)["total_contributed"] == 25_000.0


def test_an_authoritative_ledger_of_purchases_and_redemptions_nets_out(db_session):
    """A redemption is money coming back, so it reduces what was put in. Sign is preserved, never
    flipped — a redemption read as a purchase would inflate the base and flatter the return."""
    h = _household(db_session)
    inv = _fund(db_session, h.id, authoritative=True, contributions=[
        {"date": (date.today() - timedelta(days=150)).isoformat(), "amount": 40_000.0},
        {"date": (date.today() - timedelta(days=60)).isoformat(), "amount": -15_000.0},
    ])
    assert _view_of(db_session, inv)["total_contributed"] == 25_000.0


def test_an_authoritative_ledger_with_no_entries_contributed_nothing(db_session):
    """"Complete" and "empty" are allowed to be the same thing. It must read as zero, not fall back
    to inventing the instruction."""
    h = _household(db_session)
    inv = _fund(db_session, h.id, authoritative=True)
    assert _view_of(db_session, inv)["total_contributed"] == 0.0


def test_the_standing_instruction_still_reports_what_leaves_the_bank(db_session):
    """The cheap way to suppress the generator would have been to zero monthly_contribution. That
    would also have zeroed the cash-flow outflow and 'going in monthly', lying about a SIP that is
    still debiting every month."""
    h = _household(db_session)
    inv = _fund(db_session, h.id, authoritative=True)
    v = _view_of(db_session, inv)
    assert v["monthly_contribution"] == 10_000.0
    assert v["takehome_outflow"] == 10_000.0


def test_the_flag_defaults_to_false_for_every_existing_row(db_session):
    """The migration ships to a live database with fourteen rows in it. Defaulting to true would
    silently zero every accruing balance in the app."""
    h = _household(db_session)
    inv = RecurringInvestment(
        household_id=h.id, name="EPF", kind="epf", opening_balance=100_000.0,
        as_of_date=date.today() - timedelta(days=200), monthly_contribution=5_000.0,
        annual_rate_pct=8.25, compounding="annual",
    )
    db_session.add(inv)
    db_session.commit()
    assert inv.ledger_is_authoritative is False


# ---------------------------------------------------------------- the market path

def _market_fund(db_session, hid: int, *, authoritative: bool, contributions=None):
    """A fund priced off units × live NAV. This is the path every real holding takes, so the flag
    has to be honoured here too — a first version of these tests only covered the accrual path and a
    mutation that ignored the flag on THIS branch survived it."""
    from datetime import datetime, timezone
    inv = RecurringInvestment(
        household_id=hid, name="Market Flexi Cap", kind="sip", holder="Household",
        opening_balance=0.0, as_of_date=date.today() - timedelta(days=185),
        monthly_contribution=10_000.0, employer_contribution=0.0,
        annual_rate_pct=12.0, compounding="monthly", active=True, from_takehome=True,
        contributions=contributions or [], ledger_is_authoritative=authoritative,
        market={"scheme_code": "122639", "units": 100.0, "last_nav": 300.0,
                "nav_date": "18-08-2026",
                "last_nav_at": datetime.now(timezone.utc).isoformat()},
    )
    db_session.add(inv)
    db_session.commit()
    return inv


def test_a_market_fund_still_accrues_its_instruction_when_the_ledger_is_not_complete(db_session):
    h = _household(db_session)
    inv = _market_fund(db_session, h.id, authoritative=False)
    assert _view_of(db_session, inv)["total_contributed"] > 0


def test_a_market_fund_invents_nothing_once_the_ledger_is_authoritative(db_session):
    """Six months at 10,000 is ~60,000 of money that never went in. On the path every real fund
    takes, that is the whole defect."""
    h = _household(db_session)
    inv = _market_fund(db_session, h.id, authoritative=True,
                       contributions=[{"date": (date.today() - timedelta(days=100)).isoformat(),
                                       "amount": 25_000.0}])
    assert _view_of(db_session, inv)["total_contributed"] == 25_000.0


def test_an_authoritative_ledger_holds_only_the_units_it_records(db_session):
    """The anchor is a STARTING point the ledger moves from. Once the ledger is the whole history it
    already reconstructs the holding, so the anchor must not be added on top.

    This is the units half of the same defect as the invented instalments, and it was missed the
    first time: a real CAS import made every fund read at exactly TWICE the broker's value, and the
    XIRRs came out at 438% and 568%. An empty authoritative ledger therefore holds nothing, which is
    the honest reading of "these entries are the complete history"."""
    h = _household(db_session)
    inv = _market_fund(db_session, h.id, authoritative=True)
    assert _view_of(db_session, inv)["current_value"] == 0.0


def test_the_units_anchor_is_not_added_to_a_recorded_history(db_session):
    """100 anchored units and 100 recorded units are the SAME hundred. Adding them doubles the
    holding, and a doubled holding still looks plausible on screen — it was only caught because the
    total came out at exactly 2x a figure that had an independent source."""
    h = _household(db_session)
    inv = _market_fund(db_session, h.id, authoritative=True, contributions=[
        {"date": (date.today() - timedelta(days=120)).isoformat(),
         "amount": 20_000.0, "units": 100.0, "nav": 200.0},
    ])
    v = _view_of(db_session, inv)
    assert v["units"] == 100.0
    assert v["current_value"] == 30_000.0          # 100 units x NAV 300, not 200 units x 300


def test_the_anchor_is_still_the_base_when_the_ledger_is_partial(db_session):
    """Today's behaviour for every fund that has NOT been imported: the anchor is the holding and
    ledger entries move it. Breaking this would silently zero every un-imported fund."""
    h = _household(db_session)
    inv = _market_fund(db_session, h.id, authoritative=False)
    assert _view_of(db_session, inv)["units"] == 100.0
