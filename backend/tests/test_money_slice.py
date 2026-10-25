"""Money Slice Contract — the cross-tab guarantees: Plan and Analytics agree on 'this cycle' to the
rupee, account spend is counted, and variable_spend ≠ all_household_spend."""

from datetime import date

from models.tenant import Household
from models.transaction import Transaction
from services import money_slice


def _txn(hid, amount, d, category, *, card_type="credit", bank="HDFC", merchant="X"):
    return Transaction(household_id=hid, fingerprint=f"ms-{hid}-{amount}-{d}-{category}-{merchant}", date=d,
                       description=merchant, merchant=merchant, source="statement", amount=amount,
                       direction="debit", category=category, txn_type="SPEND", bank=bank,
                       card_type=card_type, card_last4="1111", stmt_month=d[:7])


def test_plan_and_money_slice_agree_on_this_cycle(db_session):
    """THE contract: Plan's per-card 'this cycle' variable total == the Money Slice instrument-cycle
    current column, to the rupee. If this holds, Plan and Analytics can never disagree on 'this cycle'."""
    from api.budget import compute_budget
    h = Household(name="R"); db_session.add(h); db_session.commit()
    today = date.today().isoformat()
    db_session.add_all([
        _txn(h.id, 5000.0, today, "Groceries"),
        _txn(h.id, 3000.0, today, "Food & Dining", card_type="ICICI Account", bank="ICICI"),  # account spend
    ]); db_session.commit()

    plan_var = round(sum(i["spent_inr"] for i in compute_budget(mode="cycle", db=db_session, household_id=h.id)["items"]), 2)
    m = money_slice.matrix(db_session, h.id, kind="variable_spend", cycle_mode="instrument_cycle")
    assert m["grand_total_by_month"][m["current"]] == plan_var == 8000.0   # incl. the account row


def test_budget_endpoint_and_cycle_matrix_endpoint_agree(db_session, client_for):
    """Airtight at the API level: /budget (Plan) cycle total == /analytics/cycle-matrix current column.
    Both now read the same Money Slice, so no tab can drift from another."""
    a = Household(name="X"); db_session.add(a); db_session.commit()
    today = date.today().isoformat()
    db_session.add_all([
        _txn(a.id, 5000.0, today, "Groceries"),
        _txn(a.id, 2000.0, today, "Food & Dining", card_type="ICICI Account", bank="ICICI"),
    ]); db_session.commit()
    c = client_for(a)
    plan_var = round(sum(i["spent_inr"] for i in c.get("/api/budget", params={"mode": "cycle"}).json()["items"]), 2)
    cm = c.get("/api/analytics/cycle-matrix").json()
    cur = cm["cycles"][-1]["key"]
    assert plan_var == cm["grand_total_by_cycle"][cur] == 7000.0


def test_account_spend_is_counted(db_session):
    """A bank-account SPEND (education fee) is real spend — the Money Slice counts it (no %account% drop)."""
    h = Household(name="A"); db_session.add(h); db_session.commit()
    db_session.add(_txn(h.id, 12000.0, "2026-06-10", "Education", card_type="ICICI Account", bank="ICICI"))
    db_session.commit()
    m = money_slice.matrix(db_session, h.id, kind="variable_spend", cycle_mode="calendar_month")
    edu = next(c for c in m["categories"] if c["category"] == "Education")
    assert edu["total"] == 12000.0      # account education spend included


def test_variable_excludes_committed_all_includes(db_session):
    """variable_spend = the 10 everyday categories (no Rent/Insurance); all_household_spend keeps them."""
    h = Household(name="V"); db_session.add(h); db_session.commit()
    db_session.add_all([
        _txn(h.id, 6000.0, "2026-06-10", "Groceries"),
        _txn(h.id, 45000.0, "2026-06-05", "Rent / Housing"),
        _txn(h.id, 2500.0, "2026-06-07", "Insurance"),
    ]); db_session.commit()

    var = {c["category"] for c in money_slice.matrix(db_session, h.id, kind="variable_spend", cycle_mode="calendar_month")["categories"]}
    alls = {c["category"] for c in money_slice.matrix(db_session, h.id, kind="all_household_spend", cycle_mode="calendar_month")["categories"]}
    assert "Groceries" in var and "Insurance" not in var and "Rent / Housing" not in var
    assert {"Groceries", "Insurance", "Rent / Housing"} <= alls
