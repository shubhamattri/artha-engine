"""Gold collateral + external (foir_exempt) loans — the money logic added for the house-buying plan.

Two invariants worth locking:
  1. A gold loan is a LIABILITY, not gold that vanished: real value never drops when you borrow; only
     pledged/net move, and marking the loan paid restores net on its own.
  2. An EXTERNAL loan (foir_exempt, e.g. a parent's gold loan) pledges the gold but is kept OUT of FOIR,
     the debt EMI total, and net-worth liabilities — while still counting as pledged.
"""
from datetime import date

from api.cashflow import compute_cashflow
from api.debt import compute_debt
from api.networth import _gold_collateral, compute_networth
from models.asset import Asset
from models.household import CashflowLine, Loan
from models.tenant import Household


def test_gold_collateral_real_value_holds_and_self_corrects():
    assets = [{"asset_type": "gold", "value_inr": 6_000_000.0}]
    gold_loan = {"category": "Gold Loan", "closed": False, "balance_inr": 1_500_000.0, "name": "SBI Gold"}

    g = _gold_collateral(assets, [gold_loan])
    assert g["real_value"] == 6_000_000.0                 # gold keeps FULL value while borrowed
    assert g["pledged_value"] == 1_500_000.0
    assert g["net_value"] == 4_500_000.0                  # equity = real − pledged
    assert g["borrow_room"] == round(6_000_000.0 * g["ltv"] - 1_500_000.0, 2)

    paid = _gold_collateral(assets, [dict(gold_loan, closed=True)])
    assert paid["pledged_value"] == 0.0                   # loan paid → un-pledged
    assert paid["net_value"] == 6_000_000.0               # net self-corrects back to full

    # a non-gold loan never pledges the gold
    other = _gold_collateral(assets, [{"category": "Personal", "closed": False, "balance_inr": 9e5, "name": "PL"}])
    assert other["pledged_value"] == 0.0


def _loan(hid, name, category, principal, rate, exempt=False):
    # A fully EXTERNAL loan (a parent's, they pay it) is foir_exempt AND not serviced by the household —
    # exactly how migration 038 backfills legacy foir_exempt loans. (A gold loan the household services is
    # the other case: foir_exempt=True but serviced_by_household=True — covered in test_gold_loan.py.)
    return Loan(household_id=hid, name=name, lender="X", holder="Asha Verma", category=category,
                original_principal=principal, annual_rate_pct=rate, tenure_months=60,
                disbursement_date=date.today(), foir_exempt=exempt,
                serviced_by_household=not exempt)


def test_foir_exempt_loan_excluded_from_cashflow_debt_and_liabilities(db_session):
    h = Household(name="H")
    db_session.add(h)
    db_session.commit()
    db_session.add(CashflowLine(household_id=h.id, kind="income", label="Salary", amount_inr=500_000, active=True))
    db_session.add(_loan(h.id, "My Car", "Car", 1_000_000, 9.0, exempt=False))
    db_session.add(_loan(h.id, "External Gold", "Gold Loan", 1_000_000, 8.65, exempt=True))
    # deterministic gold value (no live price feed in tests): manual value, no quantity
    db_session.add(Asset(household_id=h.id, name="Family gold", asset_type="gold",
                         manual_value_inr=6_000_000, active=True))
    db_session.commit()

    debt = compute_debt(db_session, h.id)
    ext = [l for l in debt["loans"] if l.get("foir_exempt")]
    assert [l["name"] for l in ext] == ["External Gold"]                    # shown, tagged
    assert all(not l.get("foir_exempt") for l in debt["loans"] if l["name"] == "My Car")

    nw = compute_networth(db_session, h.id)
    liab = [l["name"] for l in nw["liabilities"]]
    assert "My Car" in liab                                            # real household debt
    assert "External Gold" not in liab                                      # external ≠ household liability
    # …but the external gold loan DOES pledge the gold
    assert nw["gold_collateral"]["pledged_value"] == 1_000_000.0
    assert nw["gold_collateral"]["real_value"] == 6_000_000.0


def test_household_with_only_an_external_loan_has_zero_foir_emi(db_session):
    h = Household(name="H2")
    db_session.add(h)
    db_session.commit()
    db_session.add(CashflowLine(household_id=h.id, kind="income", label="Salary", amount_inr=500_000, active=True))
    db_session.add(_loan(h.id, "External Gold", "Gold Loan", 1_000_000, 8.65, exempt=True))
    db_session.commit()
    # the only loan is external → it contributes nothing to the FOIR EMI
    assert compute_cashflow(db_session, h.id)["emi"] == 0.0
    assert compute_debt(db_session, h.id)["total_emi"] == 0.0
