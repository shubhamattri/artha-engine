"""Gold loan = a running-balance account, not a fixed-EMI loan. Interest accrues monthly on the
outstanding principal; payments (any amount, any date, multiple, or none) clear accrued interest first
then chip principal. These tests pin that math (compute_gold_state) before anything is wired to it.
"""

from datetime import date

from services.loan_math import compute_gold_state


def test_no_payments_accrues_interest_principal_flat():
    # 10L @ 12% (1%/mo), 6 whole months, nothing paid → principal untouched, 6×10k interest owed.
    st, extra = compute_gold_state(1_000_000, 12.0, date(2026, 1, 1), date(2026, 7, 1), payments=[])
    assert extra["principal_outstanding"] == 1_000_000
    assert extra["interest_due"] == 60_000            # 6 × (10L × 1%)
    assert st.principal_paid == 0
    assert st.current_balance == 1_060_000            # principal + unpaid accrued interest
    assert extra["monthly_interest"] == 10_000        # interest at the CURRENT outstanding


def test_servicing_interest_only_keeps_principal_flat():
    # Pay exactly the 10k interest each month → principal stays 10L, nothing owed.
    pays = [{"date": date(2026, m, 20), "amount": 10_000} for m in range(1, 7)]
    st, extra = compute_gold_state(1_000_000, 12.0, date(2026, 1, 1), date(2026, 7, 1), payments=pays)
    assert extra["principal_outstanding"] == 1_000_000
    assert extra["interest_due"] == 0
    assert st.interest_paid == 60_000
    assert st.principal_paid == 0


def test_extra_payment_chips_principal():
    # One 60k payment after month 1's 10k interest accrues: 10k clears interest, 50k reduces principal.
    st, extra = compute_gold_state(
        1_000_000, 12.0, date(2026, 1, 1), date(2026, 2, 1),
        payments=[{"date": date(2026, 1, 20), "amount": 60_000}],
    )
    assert extra["principal_outstanding"] == 950_000
    assert st.principal_paid == 50_000
    assert extra["interest_due"] == 0


def test_skip_then_pay_double_next_month():
    # Skip month 1 (interest 10k accrues, unpaid), then pay 20k in month 2. Month-2 interest is still on
    # the full 10L (principal never dropped) = 10k. Total interest owed after 2 accruals = 20k; the 20k
    # payment clears exactly that, principal stays flat.
    st, extra = compute_gold_state(
        1_000_000, 12.0, date(2026, 1, 1), date(2026, 3, 1),
        payments=[{"date": date(2026, 2, 15), "amount": 20_000}],
    )
    assert extra["principal_outstanding"] == 1_000_000
    assert extra["interest_due"] == 0
    assert st.interest_paid == 20_000


def test_multiple_payments_same_month_both_apply():
    st, extra = compute_gold_state(
        1_000_000, 12.0, date(2026, 1, 1), date(2026, 2, 1),
        payments=[{"date": date(2026, 1, 10), "amount": 10_000},   # clears month-1 interest
                  {"date": date(2026, 1, 25), "amount": 40_000}],  # all principal
    )
    assert extra["principal_outstanding"] == 960_000
    assert st.principal_paid == 40_000
    assert extra["interest_due"] == 0


def test_payoff_projection_amortises_at_expected_payment():
    # Paid a fixed ₹8k/mo, a gold loan AMORTISES (interest shrinks as principal drops) → a long but
    # finite horizon (~390 mo), NOT the wrong 2000+ month linear projection.
    st, _ = compute_gold_state(
        1_000_000, 9.10, date(2026, 7, 9), date(2026, 7, 9),
        payments=[], expected_monthly_payment=8_000,
    )
    assert 300 < st.months_left < 500


def test_payoff_never_amortises_if_payment_below_interest():
    # ₹7k < ~₹7.58k interest → principal never drops → sentinel "never" horizon.
    st, _ = compute_gold_state(1_000_000, 9.10, date(2026, 7, 9), date(2026, 7, 9),
                               payments=[], expected_monthly_payment=7_000)
    assert st.months_left == 999


def test_closed_gold_loan_is_zeroed():
    st, extra = compute_gold_state(1_000_000, 12.0, date(2026, 1, 1), date(2026, 7, 1),
                                   payments=[], closed=True)
    assert st.current_balance == 0
    assert extra["principal_outstanding"] == 0


# ── loan_obligation dispatch (the single place standard vs flexi vs gold is decided) ──
from models.household import Loan  # noqa: E402
from models.loan_payment import LoanPayment  # noqa: E402
from services import loan_obligation  # noqa: E402


def _gold_loan(payments=None, expected=8_000):
    l = Loan(name="Family Gold Loan", lender="Meridian Gold Finance", holder="Parent", category="Gold Loan",
             original_principal=1_000_000, annual_rate_pct=9.10, tenure_months=12,
             disbursement_date=date(2026, 7, 9), loan_type="gold", foir_exempt=True,
             serviced_by_household=True, expected_monthly_payment=expected)
    l.payments = payments or []
    return l


def test_state_of_routes_gold_loan_to_gold_math():
    """The expected monthly payment is money that LEAVES THE ACCOUNT. It counts whether or not
    anyone logged it, so principal edges DOWN over an unlogged stretch rather than the balance
    climbing by the full interest every month."""
    l = _gold_loan()
    st, extra = loan_obligation.state_of(l, date(2026, 10, 9))   # +3 months, nothing logged
    assert extra["phase"] == "active"
    assert extra["principal_outstanding"] < 1_000_000             # assumed payments chipped it
    assert extra["interest_paid"] > 0                           # and cleared the accrued interest


def test_gold_monthly_emi_is_the_expected_payment_not_amortising():
    # The household budgets its chosen nominal (₹8k), NOT a formula EMI on ₹9.5L.
    l = _gold_loan(expected=8_000)
    assert loan_obligation.monthly_emi(l, date(2026, 10, 9)) == 8_000


def test_gold_payments_reduce_balance_through_obligation_layer():
    pays = [LoanPayment(date=date(2026, 7, 20), amount=100_000)]   # big extra → chips principal
    l = _gold_loan(payments=pays)
    _, extra = loan_obligation.state_of(l, date(2026, 8, 9))
    assert extra["principal_outstanding"] < 1_000_000


# ── the FOIR / cash-flow / net-worth split (serviced_by_household vs foir_exempt) ──
from models.tenant import Household  # noqa: E402


def _persist_gold(db, hid):
    l = Loan(household_id=hid, name="Family Gold", lender="Meridian Gold Finance", holder="Parent", category="Gold Loan",
             original_principal=1_000_000, annual_rate_pct=9.10, tenure_months=12,
             disbursement_date=date(2026, 7, 9), loan_type="gold",
             foir_exempt=True, serviced_by_household=True, expected_monthly_payment=8_000)
    db.add(l); db.commit(); db.refresh(l)
    return l


def _persist_external(db, hid):
    l = Loan(household_id=hid, name="Relative External", lender="Northgate Finance", holder="Relative", category="Gold Loan",
             original_principal=500_000, annual_rate_pct=12.0, tenure_months=12,
             disbursement_date=date(2026, 1, 1), foir_exempt=True, serviced_by_household=False)
    db.add(l); db.commit(); db.refresh(l)
    return l


def test_serviced_gold_emi_counts_in_cashflow(db_session):
    from api.cashflow import compute_cashflow
    h = Household(name="A"); db_session.add(h); db_session.commit()
    _persist_gold(db_session, h.id)
    assert compute_cashflow(db_session, h.id)["emi"] == 8_000   # the ₹8k IS a real outflow


def test_external_loan_stays_out_of_cashflow(db_session):
    from api.cashflow import compute_cashflow
    h = Household(name="A"); db_session.add(h); db_session.commit()
    _persist_external(db_session, h.id)
    assert compute_cashflow(db_session, h.id)["emi"] == 0        # the relative pays it → not our outflow


def test_gold_is_networth_liability_external_is_not(db_session):
    from api.networth import compute_networth
    h = Household(name="A"); db_session.add(h); db_session.commit()
    _persist_gold(db_session, h.id)
    _persist_external(db_session, h.id)
    names = {x["name"] for x in compute_networth(db_session, h.id)["liabilities"]}
    assert "Family Gold" in names           # serviced → household debt on the balance sheet
    assert "Relative External" not in names   # external → off the balance sheet


def test_debt_view_exposes_serviced_and_expected_payment(db_session):
    from api.debt import compute_debt
    h = Household(name="A"); db_session.add(h); db_session.commit()
    _persist_gold(db_session, h.id)
    loan = next(l for l in compute_debt(db_session, h.id)["loans"] if l["name"] == "Family Gold")
    assert loan["serviced_by_household"] is True
    assert loan["expected_monthly_payment"] == 8_000
    assert loan["emi_inr"] == 8_000                  # debt tab shows the nominal outflow, not interest


# ── API: create a gold loan + the flexible payment ledger (add / delete, any amount) ──
def test_create_gold_loan_via_api(db_session, client_for):
    h = Household(name="A"); db_session.add(h); db_session.commit()
    r = client_for(h).post("/api/debt", json={
        "name": "Family Gold", "lender": "Meridian Gold Finance", "holder": "Parent", "category": "Gold Loan",
        "original_principal": 1_000_000, "annual_rate_pct": 9.10, "tenure_months": 12,
        "disbursement_date": "2026-07-09", "loan_type": "gold", "foir_exempt": True,
        "serviced_by_household": True, "expected_monthly_payment": 8_000})
    assert r.status_code == 200
    body = r.json()
    assert body["loan_type"] == "gold" and body["expected_monthly_payment"] == 8_000
    assert body["payments"] == []


def test_add_and_delete_payment_via_api(db_session, client_for):
    h = Household(name="A"); db_session.add(h); db_session.commit()
    gold = _persist_gold(db_session, h.id)
    c = client_for(h)
    r = c.post(f"/api/debt/{gold.id}/payments",
               json={"date": "2026-07-20", "amount": 100_000, "note": "extra chip"})
    assert r.status_code == 200
    body = r.json()
    assert body["principal_outstanding"] < 1_000_000        # extra payment chipped principal
    assert len(body["payments"]) == 1
    pid = body["payments"][0]["id"]

    r2 = c.delete(f"/api/debt/{gold.id}/payments/{pid}")
    assert r2.status_code == 200
    # Removing the logged payment does not restore the ORIGINAL principal — it falls back to the
    # assumed expected payment for that month, which is the whole point: the money still left.
    restored = r2.json()["principal_outstanding"]
    assert restored > body["principal_outstanding"]        # the big extra payment is gone
    assert restored < 1_000_000                              # but the routine payment still counts


def test_serviced_gold_loan_is_in_debt_totals_external_is_not(db_session):
    """The Debt tab's 'Total debt / Monthly EMI / Monthly interest' are what you OWE and PAY — so a gold
    loan you service belongs there (it's on you), while a relative's external loan does not."""
    from api.debt import compute_debt
    h = Household(name="A"); db_session.add(h); db_session.commit()
    _persist_gold(db_session, h.id)        # serviced ₹10L @9.10%, you pay ₹8k
    _persist_external(db_session, h.id)    # the relative's ₹5L — not serviced by the household
    d = compute_debt(db_session, h.id)
    # Pinning the principal made this date-dependent: the balance only equals the principal on day zero,
    # and drifts as interest accrues and the expected payment chips it. Assert what the test is
    # actually about — the gold loan is IN and the external one is OUT — against the same engine
    # the app uses, so it stays true on every future run.
    gold = next(x for x in db_session.query(Loan).filter(Loan.household_id == h.id).all()
                if x.serviced_by_household)
    expected_balance = loan_obligation.state_of(gold, date.today())[0].current_balance
    assert d["total_balance"] == round(expected_balance, 2)    # gold counted...
    assert d["total_balance"] < 1_400_000                      # ...and the relative's ₹5L is NOT in there
    assert d["total_emi"] == 8_000                             # your ₹8k counted, external's excluded
    assert round(d["monthly_interest"]) == round(
        loan_obligation.state_of(gold, date.today())[1]["monthly_interest"])   # gold only


def test_gold_loan_is_a_payoff_plan_step(db_session):
    """A serviced gold loan is a first-class attackable loan — it appears in the avalanche order/steps
    like any other loan (paid at its expected ₹/mo it amortises, so the simulator handles it)."""
    from api.debt import compute_debt
    h = Household(name="A"); db_session.add(h); db_session.commit()
    _persist_gold(db_session, h.id)
    d = compute_debt(db_session, h.id)
    order_names = {o["name"] for opt in d["strategies"]["options"] for o in opt["order"]}
    assert "Family Gold" in order_names


def test_gold_loan_detail_has_no_amortisation_schedule(db_session, client_for):
    h = Household(name="A"); db_session.add(h); db_session.commit()
    gold = _persist_gold(db_session, h.id)
    detail = client_for(h).get(f"/api/debt/{gold.id}").json()
    assert detail["schedule"] == []      # a bullet gold loan doesn't amortise — no schedule to show


def test_payment_ledger_is_tenant_scoped(db_session, client_for):
    a = Household(name="A"); b = Household(name="B"); db_session.add_all([a, b]); db_session.commit()
    gold = _persist_gold(db_session, a.id)
    # B may not add a payment to A's loan
    assert client_for(b).post(f"/api/debt/{gold.id}/payments",
                              json={"date": "2026-07-20", "amount": 1000}).status_code == 404
