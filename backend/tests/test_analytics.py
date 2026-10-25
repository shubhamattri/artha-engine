"""Analytics: multi-tenant isolation (the A0 fix), exact-id drill scoping, year-on-year deltas, and
the period every response must declare. Uses the client_for factory so each request acts as one
household.

`/analytics/spend` and its five tests were removed with the endpoint, which nothing called. One of
them is worth remembering rather than mourning: it asserted that the category matrix and that
endpoint reconciled to the rupee. They do not, on real data. The matrix reads `variable_spend` (the
ten everyday categories) and that endpoint counted every SPEND row, rent and insurance included —
so the assertion held only because the fixture happened to contain neither. A check that agrees by
construction is not a check.

The other lesson outlived its endpoint too: an unparameterised call must never quietly mean
ALL TIME. It did once, and a card labelled for one month rendered the all-time figure, ~40x wrong.
Any endpoint here that grows an optional `month` needs that test written again.
"""

from datetime import date

from models.tenant import Household
from models.transaction import Transaction


def _this_month() -> str:
    t = date.today()
    return f"{t.year:04d}-{t.month:02d}"


def _stxn(hid, amount, month, category="Groceries", merchant="Shop"):
    d = f"{month}-15"
    return Transaction(household_id=hid, fingerprint=f"a-{hid}-{amount}-{d}-{category}-{merchant}", date=d,
                       description=merchant, merchant=merchant, source="statement", amount=amount,
                       direction="debit", category=category, txn_type="SPEND", bank="HDFC",
                       card_type="credit", card_last4="1111", stmt_month=month)


def _two_households(db):
    a = Household(name="A"); b = Household(name="B"); db.add_all([a, b]); db.commit()
    db.add_all([
        _stxn(a.id, 5000.0, "2026-05", "Groceries", "A-Groc"),
        _stxn(a.id, 3000.0, "2026-05", "Food & Dining", "A-Food"),
        _stxn(b.id, 99000.0, "2026-05", "Shopping", "B-Shop"),   # B's data must never leak to A
    ]); db.commit()
    return a, b


def test_analytics_matrix_is_tenant_scoped(db_session, client_for):
    a, b = _two_households(db_session)
    mx = client_for(a).get("/api/analytics/category-matrix").json()
    cats = {c["category"] for c in mx["categories"]}
    assert cats == {"Groceries", "Food & Dining"}        # only A's categories
    assert "Shopping" not in cats                          # B's spend did NOT leak
    assert mx["grand_total_by_month"]["2026-05"] == 8000.0


def test_every_analytics_response_states_its_period(db_session, client_for):
    a, _ = _two_households(db_session)
    c = client_for(a)
    for path, params in (("/api/analytics/trend", {}),
                         ("/api/analytics/category-matrix", {}), ("/api/analytics/cycle-matrix", {}),
                         ("/api/analytics/recommendations", {"narrate": False})):
        p = c.get(path, params=params).json().get("period")
        assert p and p["mode"] and p["label"], f"{path} does not declare the period it covers"


def test_trend_net_ignores_transfers_and_card_payments(db_session, client_for):
    """`net` used to be (every credit − spend), so a card-bill payment credit and an inter-account
    transfer counted as money arriving. Only INCOME is income."""
    a = Household(name="A"); db_session.add(a); db_session.commit()
    spend = _stxn(a.id, 5000.0, "2026-05", "Groceries", "spend")
    salary = _stxn(a.id, 20000.0, "2026-05", "Income", "salary")
    salary.direction, salary.txn_type = "credit", "INCOME"
    paid = _stxn(a.id, 90000.0, "2026-05", "Credit Card Payment", "cc-settle")
    paid.direction, paid.txn_type = "credit", "PAYMENT"
    db_session.add_all([spend, salary, paid]); db_session.commit()

    row = next(m for m in client_for(a).get("/api/analytics/trend").json()["months"]
               if m["month"] == "2026-05")
    assert row["total_income"] == 20000.0
    assert row["net"] == 15000.0            # 20k income − 5k spend, NOT 110k − 5k
    assert row["total_credit"] == 110000.0  # gross inflow stays available, separately labelled


def test_recommendations_are_tenant_scoped(db_session, client_for):
    a, b = _two_households(db_session)
    # B has a big Shopping history; A must never see a Shopping recommendation from B's data.
    for mth in ("2026-01", "2026-02", "2026-03"):
        db_session.add(_stxn(b.id, 20000.0, mth, "Shopping", "B-Shop"))
    db_session.commit()
    recs = client_for(a).get("/api/analytics/recommendations", params={"narrate": False}).json()
    assert all(r["category"] != "Shopping" for r in recs["recommendations"])


def test_transactions_ids_filter_is_tenant_scoped(db_session, client_for):
    a, b = _two_households(db_session)
    b_txn = db_session.query(Transaction).filter(Transaction.household_id == b.id).first()
    # A asks for B's transaction id explicitly → must get nothing (tenant filter precedes the id filter).
    out = client_for(a).get("/api/transactions", params={"ids": str(b_txn.id)}).json()
    assert out["transactions"] == [] and out["total"] == 0


def test_matrix_excludes_archived_and_cell_ids_reconcile(db_session, client_for):
    """The 369k-vs-5k bug: an archived (removed) txn must NOT inflate a matrix cell, and each cell's
    idsByMonth must point at EXACTLY the rows that sum to the cell — so a drill reconciles to the rupee."""
    a = Household(name="A"); db_session.add(a); db_session.commit()
    keep = _stxn(a.id, 5000.0, "2026-06", "Groceries", "real")
    gone = _stxn(a.id, 364000.0, "2026-06", "Groceries", "archived-oneoff"); gone.archived = True
    db_session.add_all([keep, gone]); db_session.commit()

    mx = client_for(a).get("/api/analytics/category-matrix").json()
    groc = next(c for c in mx["categories"] if c["category"] == "Groceries")
    assert groc["byMonth"]["2026-06"] == 5000.0            # archived ₹364k did NOT inflate the cell
    assert groc["idsByMonth"]["2026-06"] == [keep.id]      # only the real row's id is behind the cell

    # drilling those exact ids returns only the real ₹5,000 row → reconciles with the cell
    drill = client_for(a).get("/api/transactions", params={"ids": ",".join(map(str, groc["idsByMonth"]["2026-06"]))}).json()
    assert sum(t["amount"] for t in drill["transactions"]) == 5000.0


def test_cycle_matrix_buckets_by_billing_cycle_and_reconciles(db_session, client_for):
    """The 'This cycle' lens (per-card instrument cycle — HDFC bills on the 22nd): two spends either side
    of the 22nd fall in DIFFERENT billing cycles, and each cell reconciles exactly with its own ids."""
    a = Household(name="A"); db_session.add(a); db_session.commit()
    late = _stxn(a.id, 5000.0, "2026-06", "Groceries", "after-22nd"); late.date = "2026-06-25"   # → Jun 22 cycle
    early = _stxn(a.id, 3000.0, "2026-06", "Groceries", "before-22nd"); early.date = "2026-06-05"  # → May 22 cycle
    db_session.add_all([late, early]); db_session.commit()

    mx = client_for(a).get("/api/analytics/cycle-matrix").json()
    groc = next(c for c in mx["categories"] if c["category"] == "Groceries")
    assert len(groc["byCycle"]) == 2                       # the 15th and the 5th land in different cycles
    for key, val in groc["byCycle"].items():               # every cell reconciles with its own ids
        ids = groc["idsByCycle"][key]
        drill = client_for(a).get("/api/transactions", params={"ids": ",".join(map(str, ids))}).json()
        assert round(sum(t["amount"] for t in drill["transactions"]), 2) == val


def test_year_on_year_delta_present_only_with_prior_year(db_session, client_for):
    a = Household(name="Y"); db_session.add(a); db_session.commit()
    db_session.add_all([
        _stxn(a.id, 10000.0, "2025-06", "Groceries"),   # prior year
        _stxn(a.id, 12000.0, "2026-06", "Groceries"),   # +20% YoY
        _stxn(a.id, 8000.0, "2026-05", "Groceries"),    # no 2025-05 → no YoY
    ]); db_session.commit()
    mx = client_for(a).get("/api/analytics/category-matrix").json()
    groc = next(c for c in mx["categories"] if c["category"] == "Groceries")
    assert groc["yoyByMonth"]["2026-06"] == 20.0        # 10k → 12k
    assert "2026-05" not in groc["yoyByMonth"]          # no prior-year month → absent, not fabricated
