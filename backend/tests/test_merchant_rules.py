"""Canonical merchant consistency: known brands resolve to one category; the backfill fixes existing
rows but never overrides a merchant the user set; ambiguous payers (Amazon) are left alone."""

from models.category_rule import CategoryRule
from models.tenant import Household
from models.transaction import Transaction
from services import merchant_rules
from services.learned import signature


def _t(hid, amount, category, merchant):
    return Transaction(household_id=hid, fingerprint=f"m-{hid}-{amount}-{merchant}", date="2026-06-10",
                       description=merchant, merchant=merchant, source="statement", amount=amount,
                       direction="debit", category=category, txn_type="SPEND", bank="HDFC",
                       card_type="credit", card_last4="1111", stmt_month="2026-06")


def test_merchant_review_lists_splits_and_fix_pins_it(db_session, client_for):
    """The review screen: a merchant split across categories is surfaced; pinning it recategorises every
    transaction and remembers a USER rule so it stays fixed."""
    from services.learned import classify_text, signature
    h = Household(name="RV"); db_session.add(h); db_session.commit()
    db_session.add_all([
        _t(h.id, 6000.0, "Shopping", "Peoples Mini Mart"),
        _t(h.id, 400.0, "Food & Dining", "Peoples Mini Mart"),   # same merchant, 2 categories
    ]); db_session.commit()
    c = client_for(h)

    review = c.get("/api/transactions/category-consistency").json()["merchants"]
    assert any(m["key"] == "peoples mini" and len(m["categories"]) == 2 for m in review)

    out = c.post("/api/transactions/merchant-category", json={"key": "peoples mini", "category": "Groceries"}).json()
    assert out["changed"] == 2                              # both the Shopping and Food rows → Groceries
    cats = {t.category for t in db_session.query(Transaction).filter(Transaction.household_id == h.id)}
    assert cats == {"Groceries"}                            # both rows now Groceries
    # remembered as a user rule → future txns stick
    assert classify_text(db_session, "Peoples Mini Mart", h.id)["category"] == "Groceries"
    from models.category_rule import CategoryRule as CR
    r = db_session.query(CR).filter(CR.signature == signature("Peoples Mini Mart")).first()
    assert r.kind == "user" and r.category == "Groceries"


def test_resolve_known_brands_and_leaves_ambiguous():
    assert merchant_rules.resolve("Bundl Technologies Pri")["category"] == "Food & Dining"
    assert merchant_rules.resolve("SWIGGY LTD")["category"] == "Food & Dining"
    assert merchant_rules.resolve("Blink Commerce Pvt")["category"] == "Groceries"
    assert merchant_rules.resolve("NETFLIX.COM")["category"] == "Entertainment & Subscriptions"
    assert merchant_rules.resolve("Urban Company")["category"] == "Personal / Misc"
    assert merchant_rules.resolve("AMAZON PAY INDIA") is None   # genuinely multi-category → untouched
    # specificity: 'Swiggy Instamart' is quick-commerce GROCERIES, not Swiggy food delivery — the more
    # specific 'instamart' rule must win over the broad 'swiggy' rule.
    assert merchant_rules.resolve("SWIGGY INSTAMART BENGALURU")["category"] == "Groceries"
    assert merchant_rules.resolve("Swiggy Ltd")["category"] == "Food & Dining"


def test_apply_corrects_brands_even_over_a_wrong_rule(db_session):
    """A brand's category is objective, so the canonical map corrects a stale/wrong AI-learned rule
    and repairs it so the memory stops fighting.

    CHANGED: it no longer overrides a rule the USER set. Explicit user input outranks a heuristic —
    a household that deliberately books Swiggy as Groceries (they order via Instamart) was having
    that reverted on every sweep, which is why the same correction had to be made repeatedly.
    See test_user_correction_sticks.py. AI guesses are still corrected freely."""
    h = Household(name="H"); db_session.add(h); db_session.commit()
    db_session.add_all([
        _t(h.id, 500.0, "Entertainment & Subscriptions", "Bundl Technologies"),   # wrong → Food
        _t(h.id, 300.0, "Bills & Utilities", "Bundl Techn"),                       # wrong → Food
        _t(h.id, 900.0, "Shopping", "Swiggy Ltd"),                                 # wrong (user rule) → Food
        _t(h.id, 200.0, "Groceries", "Blinkit"),                                   # already right → no-op
    ]); db_session.commit()
    db_session.add(CategoryRule(household_id=h.id, signature=signature("Swiggy Ltd"),
                                category="Shopping", kind="ai"))   # a wrong AI guess
    db_session.commit()

    out = merchant_rules.apply(db_session, h.id)
    cats = {t.merchant: t.category for t in db_session.query(Transaction).filter(Transaction.household_id == h.id)}
    assert cats["Bundl Technologies"] == "Food & Dining"
    assert cats["Bundl Techn"] == "Food & Dining"
    assert cats["Swiggy Ltd"] == "Food & Dining"    # canonical brand corrects a wrong AI guess
    assert cats["Blinkit"] == "Groceries"           # unchanged
    assert out["changed"] == 3
    # the wrong rule was repaired to the canonical category
    fixed = db_session.query(CategoryRule).filter(CategoryRule.signature == signature("Swiggy Ltd")).first()
    assert fixed.category == "Food & Dining" and fixed.kind == "rule"
