"""A household manages EVERY category, not only the ones it invented.

Category management first landed against custom_categories, which is a table, while the built-in
taxonomy is a Python enum. That split is an implementation detail and it reached the screen: a
household using only built-ins opened "Manage categories" and found nothing, while Groceries and
Travel sat on the Plan behind it.

One thing genuinely cannot work the same for both, and it is not a preference. Forty places compare
against Category.*.value and the AI classifier's prompt embeds those names as literal text, so
rewriting a built-in's stored name would drop it out of the Plan pool and change what the model is
told. A built-in gets a LABEL over a stable key. A custom category, whose name is its identity, keeps
its real rewrite.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from models.custom_category import CustomCategory
from models.tenant import Household
from models.transaction import Transaction


def _client(db_session, household):
    from api import categories as cat_api
    from api.deps import get_current_household
    from database import get_db
    app = FastAPI()
    app.include_router(cat_api.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_household] = lambda: household
    return TestClient(app)


def _household(db_session) -> Household:
    h = Household(name="ALL")
    db_session.add(h)
    db_session.commit()
    return h


def _txn(db_session, hid: int, category: str, amount: float = 700.0) -> Transaction:
    t = Transaction(household_id=hid, fingerprint=f"all-{category}-{amount}", date="2026-02-10",
                    description="Shop", merchant="Shop", source="statement", amount=amount,
                    direction="debit", category=category, txn_type="SPEND", bank="HDFC",
                    card_type="credit", card_last4="1111", stmt_month="2026-02")
    db_session.add(t)
    db_session.commit()
    return t


def test_the_manage_list_contains_built_ins_not_just_your_own(db_session):
    """The bug, stated as a test: a household with no categories of its own still has categories."""
    h = _household(db_session)
    r = _client(db_session, h).get("/api/categories/all")
    assert r.status_code == 200
    names = [c["category"] for c in r.json()["categories"]]
    assert "Groceries" in names and "Travel" in names and "Personal / Misc" in names
    assert all(c["builtin"] for c in r.json()["categories"]), "nothing custom exists here yet"


def test_your_own_categories_appear_in_the_same_list_marked_as_yours(db_session):
    h = _household(db_session)
    db_session.add(CustomCategory(household_id=h.id, name="Weekend trips", sort_order=0))
    db_session.commit()

    cats = _client(db_session, h).get("/api/categories/all").json()["categories"]
    mine = next(c for c in cats if c["category"] == "Weekend trips")
    assert mine["builtin"] is False
    assert next(c for c in cats if c["category"] == "Groceries")["builtin"] is True


def test_renaming_a_built_in_is_a_label_and_never_moves_the_key(db_session):
    """The whole reason built-ins and custom categories differ. Rewriting the stored name would drop
    the category out of the Plan pool and change the text the classifier is prompted with."""
    from services.categories import is_variable
    h = _household(db_session)
    _txn(db_session, h.id, "Groceries", 2_500.0)

    r = _client(db_session, h).patch("/api/categories/by-name/Groceries/properties",
                                     json={"display_name": "Kirana"})
    assert r.status_code == 200, r.text
    assert r.json()["display_name"] == "Kirana"
    assert r.json()["category"] == "Groceries", "the key does not move"

    assert db_session.query(Transaction).one().category == "Groceries", "history untouched"
    assert is_variable("Groceries", db_session, h.id) is True, "still in the Plan pool"


def test_clearing_the_label_shows_the_real_name_again(db_session):
    """A rename that cannot be undone is a trap, and for a built-in the canonical name is always there
    to fall back to."""
    h = _household(db_session)
    c = _client(db_session, h)
    c.patch("/api/categories/by-name/Groceries/properties", json={"display_name": "Kirana"})
    r = c.patch("/api/categories/by-name/Groceries/properties", json={"display_name": ""})
    assert r.json()["display_name"] == "Groceries"


def test_a_built_in_can_be_marked_committed_and_put_back(db_session):
    """Travel is everyday spend for one household and a committed annual cost for another. Setting it
    back to null means "whatever the taxonomy says", which is not the same as forcing it True."""
    from services.categories import is_variable
    h = _household(db_session)
    c = _client(db_session, h)

    c.patch("/api/categories/by-name/Travel/properties", json={"is_variable": False})
    assert is_variable("Travel", db_session, h.id) is False

    c.patch("/api/categories/by-name/Travel/properties", json={"is_variable": None})
    assert is_variable("Travel", db_session, h.id) is True, "back to the taxonomy's own answer"


def test_a_built_in_takes_a_colour_and_a_default_target(db_session):
    h = _household(db_session)
    r = _client(db_session, h).patch("/api/categories/by-name/Health/properties",
                                     json={"color": "#B07D1E", "default_target_inr": 4_000})
    assert r.status_code == 200, r.text
    assert r.json()["color"] == "#B07D1E"
    assert r.json()["default_target_inr"] == 4_000.0


def test_a_negative_target_is_refused_on_a_built_in_too(db_session):
    h = _household(db_session)
    r = _client(db_session, h).patch("/api/categories/by-name/Health/properties",
                                     json={"default_target_inr": -5})
    assert r.status_code == 400


def test_an_unknown_category_name_is_refused(db_session):
    """Otherwise a typo would create settings for a category that does not exist, which would then sit
    in the table forever applying to nothing."""
    h = _household(db_session)
    r = _client(db_session, h).patch("/api/categories/by-name/Grocerie/properties",
                                     json={"color": "#000000"})
    assert r.status_code == 404


def test_ordering_covers_built_ins_and_your_own_together(db_session):
    h = _household(db_session)
    db_session.add(CustomCategory(household_id=h.id, name="Weekend trips", sort_order=0))
    db_session.commit()
    c = _client(db_session, h)

    r = c.post("/api/categories/order", json={"names": ["Weekend trips", "Groceries"]})
    assert r.status_code == 200, r.text
    cats = c.get("/api/categories/all").json()["categories"]
    assert [x["category"] for x in cats][:2] == ["Weekend trips", "Groceries"]


def test_renaming_a_custom_category_carries_its_settings_across(db_session):
    """Settings are keyed by canonical name, and a custom rename really does move that name. Without
    carrying the row the category silently loses its colour and target the moment it is renamed."""
    h = _household(db_session)
    row = CustomCategory(household_id=h.id, name="Weekend trips", sort_order=0)
    db_session.add(row)
    db_session.commit()
    c = _client(db_session, h)
    c.patch("/api/categories/by-name/Weekend trips/properties", json={"color": "#0F7A52"})

    assert c.patch(f"/api/categories/{row.id}", json={"name": "Short breaks"}).status_code == 200
    cats = c.get("/api/categories/all").json()["categories"]
    moved = next(x for x in cats if x["category"] == "Short breaks")
    assert moved["color"] == "#0F7A52", "the colour followed the rename"


# --- the target you can see is the target you can edit -----------------------------

def _budget(db_session, hid: int, category: str, amount: float, month: str = "2026-08"):
    from models.household import Budget
    b = Budget(household_id=hid, category=category, month=month, monthly_limit_inr=amount)
    db_session.add(b)
    db_session.commit()
    return b


def test_a_category_reports_the_target_the_plan_shows(db_session):
    """Every row read "no target" while the Plan next to it showed Groceries at 40,000. A target lives
    per month in `budgets`; default_target_inr only seeds months with no row, and nobody had ever set
    one. The screen was truthfully answering a question nobody asked."""
    from datetime import date

    from services.categories import all_manageable_categories
    h = _household(db_session)
    _budget(db_session, h.id, "Groceries", 40_000.0, month=date.today().strftime("%Y-%m"))

    cats = all_manageable_categories(db_session, h.id)
    groceries = next(c for c in cats if c["category"] == "Groceries")
    assert groceries["current_target_inr"] == 40_000.0
    assert groceries["default_target_inr"] is None, "still no DEFAULT, and that is a different thing"


def test_a_month_with_no_budget_row_reports_none_not_zero(db_session):
    """None means "nothing set"; 0.0 means someone deliberately budgeted nothing. Collapsing them
    would make an untouched category look like a decision."""
    from services.categories import all_manageable_categories
    h = _household(db_session)
    cats = all_manageable_categories(db_session, h.id)
    assert next(c for c in cats if c["category"] == "Groceries")["current_target_inr"] is None


def test_setting_a_target_changes_what_the_plan_shows_this_month(db_session):
    """Writing only the default meant typing a number here did nothing until next month, and the field
    still read "no target" immediately after being saved."""
    from services.categories import all_manageable_categories
    h = _household(db_session)
    r = _client(db_session, h).patch("/api/categories/by-name/Groceries/properties",
                                     json={"default_target_inr": 40_000})
    assert r.status_code == 200, r.text

    groceries = next(c for c in all_manageable_categories(db_session, h.id)
                     if c["category"] == "Groceries")
    assert groceries["current_target_inr"] == 40_000.0, "the Plan's own figure moved"
    assert groceries["default_target_inr"] == 40_000.0, "and it seeds future months too"


def test_clearing_a_target_removes_this_month_s_row(db_session):
    from datetime import date

    from models.household import Budget
    from services.categories import all_manageable_categories
    h = _household(db_session)
    _budget(db_session, h.id, "Groceries", 40_000.0, month=date.today().strftime("%Y-%m"))

    _client(db_session, h).patch("/api/categories/by-name/Groceries/properties",
                                 json={"default_target_inr": None})
    assert db_session.query(Budget).filter(Budget.category == "Groceries").count() == 0
    groceries = next(c for c in all_manageable_categories(db_session, h.id)
                     if c["category"] == "Groceries")
    assert groceries["current_target_inr"] is None
