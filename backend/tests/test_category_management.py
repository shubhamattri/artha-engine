"""Managing a household's own categories: archive, merge, reorder, colour, split, and what each does
to the history already filed under them.

A category could be created, renamed and deleted. Everything a household actually does with its own
taxonomy over time had nowhere to live, and the one destructive option available — delete — always
moved the past into Personal / Misc whether or not that was true.

The rule these tests exist to hold: a category's PAST is a record of what happened. Retiring a
category may change what the pickers offer and what the Plan counts going forward, but it may never
quietly restate where money went.
"""

from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient
from models.category_rule import CategoryRule
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
    h = Household(name="CM")
    db_session.add(h)
    db_session.commit()
    return h


def _cat(db_session, hid: int, name: str, **kw) -> CustomCategory:
    c = CustomCategory(household_id=hid, name=name, sort_order=kw.pop("sort_order", 0), **kw)
    db_session.add(c)
    db_session.commit()
    return c


def _txn(db_session, hid: int, category: str, amount: float = 500.0,
         merchant: str = "Shop", d: str = "2026-02-10") -> Transaction:
    t = Transaction(household_id=hid, fingerprint=f"cm-{category}-{merchant}-{amount}-{d}", date=d,
                    description=merchant, merchant=merchant, source="statement", amount=amount,
                    direction="debit", category=category, txn_type="SPEND", bank="HDFC",
                    card_type="credit", card_last4="1111", stmt_month=d[:7])
    db_session.add(t)
    db_session.commit()
    return t


def _rule(db_session, hid: int, signature: str, category: str, merchant: str) -> CategoryRule:
    r = CategoryRule(household_id=hid, signature=signature, category=category,
                     merchant=merchant, kind="user", hits=3)
    db_session.add(r)
    db_session.commit()
    return r


# --- archive ---------------------------------------------------------------------

def test_archiving_hides_a_category_from_pickers_without_touching_its_history(db_session):
    """The honest alternative to delete. Deleting moves the past into another bucket, which says the
    money went somewhere it did not; archiving leaves every past figure exactly where it was."""
    from services.categories import custom_category_names
    h = _household(db_session)
    _cat(db_session, h.id, "Hobbies")
    _txn(db_session, h.id, "Hobbies", 1_200.0)

    r = _client(db_session, h).patch("/api/categories/by-name/Hobbies/properties", json={"active": False})
    assert r.status_code == 200 and r.json()["active"] is False

    # The taxonomy still knows the name, which is what keeps history readable. Narrowing happens in
    # the picker (/transactions/categories), which reads the archived flag from category_settings.
    assert "Hobbies" in custom_category_names(db_session, h.id), "still known, so history still reads"
    survivor = db_session.query(Transaction).filter(Transaction.category == "Hobbies").one()
    assert survivor.amount == 1_200.0, "the past is untouched"


def test_an_archived_category_still_counts_in_the_plan_pool(db_session):
    """Archiving must not move last month's totals. A household tidying its list is not restating what
    it spent, and a Plan figure that shifts because of a housekeeping action is the worse surprise."""
    from services.categories import variable_category_values
    h = _household(db_session)
    _cat(db_session, h.id, "Hobbies")
    _client(db_session, h).patch("/api/categories/by-name/Hobbies/properties", json={"active": False})
    assert "Hobbies" in variable_category_values(db_session, h.id)


# --- fixed vs variable -----------------------------------------------------------

def test_marking_a_category_committed_takes_it_out_of_the_everyday_pool(db_session):
    """A household's own "Rent" is not everyday spend. Judging it against a weekly pace is the same
    error as budgeting the EMI, and this one is MEANT to change past months: it says the category was
    never variable, so a Plan that kept counting it was wrong about history, not merely untidy."""
    from services.categories import is_variable, variable_category_values
    h = _household(db_session)
    _cat(db_session, h.id, "School fees")
    assert is_variable("School fees", db_session, h.id) is True

    _client(db_session, h).patch("/api/categories/by-name/School fees/properties", json={"is_variable": False})
    assert is_variable("School fees", db_session, h.id) is False
    assert "School fees" not in variable_category_values(db_session, h.id)


# --- merge -----------------------------------------------------------------------

def test_merging_moves_transactions_rules_and_targets_then_retires_the_source(db_session):
    """"Food" and "Food & Dining" both existing is the case this is for."""
    h = _household(db_session)
    src = _cat(db_session, h.id, "Food")
    _cat(db_session, h.id, "Food & Dining", sort_order=1)
    _txn(db_session, h.id, "Food", 800.0, merchant="Dhaba")
    _rule(db_session, h.id, "dhaba", "Food", "Dhaba")

    r = _client(db_session, h).post(f"/api/categories/{src.id}/merge", json={"into": "Food & Dining"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["merged_into"] == "Food & Dining"
    assert body["transactions_moved"] == 1 and body["rules_moved"] == 1

    assert db_session.query(CustomCategory).filter_by(name="Food").first() is None
    assert db_session.query(Transaction).one().category == "Food & Dining"
    assert db_session.query(CategoryRule).one().category == "Food & Dining"


def test_a_category_cannot_merge_into_itself(db_session):
    h = _household(db_session)
    c = _cat(db_session, h.id, "Food")
    r = _client(db_session, h).post(f"/api/categories/{c.id}/merge", json={"into": "Food"})
    assert r.status_code == 400


def test_merging_into_an_unknown_name_is_refused_rather_than_inventing_a_bucket(db_session):
    """Silently creating the target would let a typo become a category holding real history."""
    h = _household(db_session)
    c = _cat(db_session, h.id, "Food")
    _txn(db_session, h.id, "Food", 800.0)
    r = _client(db_session, h).post(f"/api/categories/{c.id}/merge", json={"into": "Fod & Dning"})
    assert r.status_code == 400
    assert db_session.query(Transaction).one().category == "Food", "nothing moved"


# --- delete with a chosen target -------------------------------------------------

def test_delete_can_send_history_somewhere_other_than_personal_misc(db_session):
    h = _household(db_session)
    c = _cat(db_session, h.id, "Takeaway")
    _cat(db_session, h.id, "Food & Dining", sort_order=1)
    _txn(db_session, h.id, "Takeaway", 300.0)

    r = _client(db_session, h).delete(f"/api/categories/{c.id}?into=Food %26 Dining")
    assert r.status_code == 200, r.text
    assert r.json()["reassigned_to"] == "Food & Dining"
    assert db_session.query(Transaction).one().category == "Food & Dining"


def test_delete_still_defaults_to_personal_misc(db_session):
    """The old behaviour is the default, so nothing that called this before changes meaning."""
    h = _household(db_session)
    c = _cat(db_session, h.id, "Takeaway")
    _txn(db_session, h.id, "Takeaway", 300.0)
    r = _client(db_session, h).delete(f"/api/categories/{c.id}")
    assert r.json()["reassigned_to"] == "Personal / Misc"
    assert db_session.query(Transaction).one().category == "Personal / Misc"


# --- reorder ---------------------------------------------------------------------

def test_reorder_sets_display_order_and_leaves_unlisted_ones_after(db_session):
    """A partial list from a drag-and-drop must not silently reshuffle what it did not mention."""
    h = _household(db_session)
    _cat(db_session, h.id, "Alpha", sort_order=0)
    _cat(db_session, h.id, "Beta", sort_order=1)
    _cat(db_session, h.id, "Gamma", sort_order=2)

    cl = _client(db_session, h)
    r = cl.post("/api/categories/order", json={"names": ["Gamma", "Alpha"]})
    assert r.status_code == 200, r.text
    ordered = [x["category"] for x in cl.get("/api/categories/all").json()["categories"]]
    assert ordered[:2] == ["Gamma", "Alpha"]
    assert "Beta" in ordered, "the one not mentioned is still there, just after"


def test_reorder_refuses_ids_from_another_household(db_session):
    h1, h2 = _household(db_session), Household(name="other")
    db_session.add(h2)
    db_session.commit()
    _cat(db_session, h1.id, "Mine")
    _cat(db_session, h2.id, "Theirs")
    r = _client(db_session, h1).post("/api/categories/order", json={"names": ["Mine", "Theirs"]})
    assert r.status_code == 400, "another household's category is not a name this one can order"


# --- colour and default target ---------------------------------------------------

def test_a_colour_sticks_to_the_category(db_session):
    """The palette is indexed by position, so without this adding a category recoloured every one
    listed after it."""
    h = _household(db_session)
    _cat(db_session, h.id, "Weekend trips")
    r = _client(db_session, h).patch("/api/categories/by-name/Weekend trips/properties", json={"color": "#0F7A52"})
    assert r.status_code == 200 and r.json()["color"] == "#0F7A52"


def test_a_default_target_seeds_a_month_that_has_no_budget_row(db_session):
    from api.budget import compute_budget
    h = _household(db_session)
    _cat(db_session, h.id, "Weekend trips")
    _client(db_session, h).patch("/api/categories/by-name/Weekend trips/properties",
                                 json={"default_target_inr": 6_000})

    bud = compute_budget(month=date.today().strftime("%Y-%m"), mode="month",
                         db=db_session, household_id=h.id)
    item = next(i for i in bud["items"] if i["category"] == "Weekend trips")
    assert item["limit_inr"] == 6_000.0


def test_a_negative_default_target_is_refused(db_session):
    h = _household(db_session)
    _cat(db_session, h.id, "Weekend trips")
    r = _client(db_session, h).patch("/api/categories/by-name/Weekend trips/properties",
                                     json={"default_target_inr": -1})
    assert r.status_code == 400


# --- rules and split -------------------------------------------------------------

def test_the_rules_feeding_a_category_are_visible_with_why_and_how_often(db_session):
    """Categorisation is otherwise a black box: a transaction appears under a heading and nothing says
    whether that came from something you confirmed or something a model guessed."""
    h = _household(db_session)
    c = _cat(db_session, h.id, "Groceries+")
    _rule(db_session, h.id, "blinkit", "Groceries+", "Blinkit")
    _rule(db_session, h.id, "bigbasket", "Groceries+", "BigBasket")

    r = _client(db_session, h).get(f"/api/categories/{c.id}/rules")
    assert r.status_code == 200
    rules = r.json()["rules"]
    assert {x["merchant"] for x in rules} == {"Blinkit", "BigBasket"}
    assert all(x["kind"] == "user" for x in rules)


def test_splitting_moves_the_chosen_merchants_and_their_past(db_session):
    """A new category that starts empty and only fills from future spend is why everything unclear ends
    up in Personal / Misc forever."""
    h = _household(db_session)
    src = _cat(db_session, h.id, "Online orders")
    _rule(db_session, h.id, "blinkit", "Online orders", "Blinkit")
    _rule(db_session, h.id, "myntra", "Online orders", "Myntra")
    _txn(db_session, h.id, "Online orders", 900.0, merchant="Blinkit")
    _txn(db_session, h.id, "Online orders", 2_500.0, merchant="Myntra", d="2026-02-11")

    r = _client(db_session, h).post(f"/api/categories/{src.id}/split",
                                    json={"name": "Groceries+", "signatures": ["blinkit"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rules_moved"] == 1 and body["transactions_moved"] == 1

    moved = db_session.query(Transaction).filter(Transaction.merchant == "Blinkit").one()
    stayed = db_session.query(Transaction).filter(Transaction.merchant == "Myntra").one()
    assert moved.category == "Groceries+"
    assert stayed.category == "Online orders", "only what was selected moves"


def test_splitting_out_nothing_is_refused(db_session):
    h = _household(db_session)
    src = _cat(db_session, h.id, "Online orders")
    r = _client(db_session, h).post(f"/api/categories/{src.id}/split",
                                    json={"name": "Groceries+", "signatures": []})
    assert r.status_code == 400


def test_a_split_inherits_whether_the_source_was_committed(db_session):
    """Splitting "School fees" must not produce an everyday-spend bucket that the Plan starts judging
    against a weekly pace."""
    h = _household(db_session)
    src = _cat(db_session, h.id, "School fees")
    _rule(db_session, h.id, "bus", "School fees", "Bus")
    cl = _client(db_session, h)
    cl.patch("/api/categories/by-name/School fees/properties", json={"is_variable": False})

    r = cl.post(f"/api/categories/{src.id}/split",
                json={"name": "School transport", "signatures": ["bus"]})
    assert r.status_code == 200, r.text
    from services.categories import is_variable
    assert is_variable("School transport", db_session, h.id) is False
