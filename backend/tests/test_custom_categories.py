"""User-defined budget categories — proves a custom category is plugged in EVERYWHERE a built-in one is
(picker, Plan, variable-spend analytics) and that rename/delete rewrite existing data with no orphans.
This is the regression guard against the 'half-wired' failure: add a category and half the app ignores it.
"""

from models.custom_category import CustomCategory
from models.household import Budget
from models.tenant import Household
from models.transaction import Transaction

PERSONAL = "Personal / Misc"


def _stxn(hid, amount, month, category, merchant="Shop"):
    d = f"{month}-15"
    return Transaction(household_id=hid, fingerprint=f"c-{hid}-{amount}-{d}-{category}-{merchant}", date=d,
                       description=merchant, merchant=merchant, source="statement", amount=amount,
                       direction="debit", category=category, txn_type="SPEND", bank="HDFC",
                       card_type="credit", card_last4="1111", stmt_month=month)


def _hh(db, name="A"):
    h = Household(name=name); db.add(h); db.commit()
    return h


def test_create_shows_in_picker_as_spend(db_session, client_for):
    a = _hh(db_session)
    c = client_for(a)
    r = c.post("/api/categories", json={"name": "Pet Care"})
    assert r.status_code == 200 and r.json()["name"] == "Pet Care"

    cats = c.get("/api/transactions/categories").json()["categories"]
    pet = next((x for x in cats if x["value"] == "Pet Care"), None)
    assert pet is not None, "custom category must appear in the shared picker endpoint"
    assert pet["is_spend"] is True and pet["custom"] is True


def test_custom_category_appears_on_plan_even_with_no_spend(db_session, client_for):
    a = _hh(db_session)
    c = client_for(a)
    c.post("/api/categories", json={"name": "Pet Care"})
    items = c.get("/api/budget").json()["items"]
    assert any(it["category"] == "Pet Care" for it in items), \
        "a fresh custom category must be present on the Plan so a target can be set"


def test_custom_spend_counts_in_variable_analytics(db_session, client_for):
    """A SPEND transaction tagged with a custom category must be inside 'variable_spend' — otherwise the
    Plan's spent column and Analytics silently drop it (the money_slice loose end)."""
    a = _hh(db_session)
    c = client_for(a)
    c.post("/api/categories", json={"name": "Pet Care"})
    db_session.add(_stxn(a.id, 4000.0, "2026-05", "Pet Care", "VetClinic"))
    db_session.add(_stxn(a.id, 1000.0, "2026-05", "Groceries", "Shop"))
    db_session.commit()

    mx = c.get("/api/analytics/category-matrix").json()
    cats = {x["category"]: x for x in mx["categories"]}
    assert "Pet Care" in cats, "custom-category spend must show in the variable-spend matrix"
    assert cats["Pet Care"]["total"] == 4000.0
    assert mx["grand_total_by_month"]["2026-05"] == 5000.0   # 4000 custom + 1000 built-in


def test_rename_rewrites_all_references(db_session, client_for):
    a = _hh(db_session)
    c = client_for(a)
    cat_id = c.post("/api/categories", json={"name": "Pet Care"}).json()["id"]
    db_session.add(_stxn(a.id, 4000.0, "2026-05", "Pet Care", "VetClinic"))
    db_session.add(Budget(household_id=a.id, category="Pet Care", monthly_limit_inr=5000.0, month="2026-05"))
    db_session.commit()

    r = c.patch(f"/api/categories/{cat_id}", json={"name": "Pets & Vet"})
    assert r.status_code == 200

    # no transaction, budget or picker entry may still point at the old name
    assert db_session.query(Transaction).filter(Transaction.category == "Pet Care").count() == 0
    assert db_session.query(Transaction).filter(Transaction.category == "Pets & Vet").count() == 1
    assert db_session.query(Budget).filter(Budget.category == "Pets & Vet").count() == 1
    values = {x["value"] for x in c.get("/api/transactions/categories").json()["categories"]}
    assert "Pets & Vet" in values and "Pet Care" not in values


def test_delete_reassigns_to_personal_misc(db_session, client_for):
    a = _hh(db_session)
    c = client_for(a)
    cat_id = c.post("/api/categories", json={"name": "Pet Care"}).json()["id"]
    db_session.add(_stxn(a.id, 4000.0, "2026-05", "Pet Care", "VetClinic"))
    db_session.commit()

    r = c.delete(f"/api/categories/{cat_id}")
    assert r.status_code == 200 and r.json()["reassigned_to"] == PERSONAL

    assert db_session.query(CustomCategory).count() == 0
    # the spend is preserved, now under Personal / Misc (nothing orphaned or lost)
    assert db_session.query(Transaction).filter(Transaction.category == "Pet Care").count() == 0
    moved = db_session.query(Transaction).filter(Transaction.category == PERSONAL).one()
    assert moved.amount == 4000.0


def test_delete_merges_budget_into_personal(db_session, client_for):
    a = _hh(db_session)
    c = client_for(a)
    cat_id = c.post("/api/categories", json={"name": "Pet Care"}).json()["id"]
    db_session.add(Budget(household_id=a.id, category="Pet Care", monthly_limit_inr=3000.0, month="2026-05"))
    db_session.add(Budget(household_id=a.id, category=PERSONAL, monthly_limit_inr=2000.0, month="2026-05"))
    db_session.commit()

    c.delete(f"/api/categories/{cat_id}")
    rows = db_session.query(Budget).filter(Budget.month == "2026-05").all()
    # the two rows fold into one Personal/Misc target (respects the category+month uniqueness)
    assert len(rows) == 1 and rows[0].category == PERSONAL and rows[0].monthly_limit_inr == 5000.0


def test_validation_rejects_builtin_empty_and_duplicate(db_session, client_for):
    a = _hh(db_session)
    c = client_for(a)
    assert c.post("/api/categories", json={"name": "   "}).status_code == 400
    assert c.post("/api/categories", json={"name": "Groceries"}).status_code == 409   # built-in clash
    c.post("/api/categories", json={"name": "Pet Care"})
    assert c.post("/api/categories", json={"name": "pet care"}).status_code == 409     # dup (case-insensitive)


def test_custom_categories_are_tenant_scoped(db_session, client_for):
    a = _hh(db_session, "A"); b = _hh(db_session, "B")
    client_for(a).post("/api/categories", json={"name": "Pet Care"})
    # B must not see A's custom category in its picker or its manage list
    b_values = {x["value"] for x in client_for(b).get("/api/transactions/categories").json()["categories"]}
    assert "Pet Care" not in b_values
    # Asserted against /categories/all, the list the manage screen actually renders. It was checked
    # against GET /categories, which was removed with this change because nothing called it — and a
    # leak that only shows on an endpoint no screen uses is a leak nobody would ever see.
    b_manage = {c["category"] for c in client_for(b).get("/api/categories/all").json()["categories"]}
    assert "Pet Care" not in b_manage
