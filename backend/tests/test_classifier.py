"""The unified classifier: one path (merchant_rules → learned → keyword rules) for every domain."""

from models.tenant import Household
from services.classifier import classify


def test_canonical_brand_wins_and_direction_sets_type(db_session):
    h = Household(name="H"); db_session.add(h); db_session.commit()
    cat, conf, tt = classify(db_session, "Bundl Technologies Pri", "debit", h.id)
    assert cat == "Food & Dining" and tt == "SPEND"          # brand map wins
    cat2, _, tt2 = classify(db_session, "Netflix", "debit", h.id)
    assert cat2 == "Entertainment & Subscriptions" and tt2 == "SPEND"


def test_credit_never_becomes_spend(db_session):
    h = Household(name="H2"); db_session.add(h); db_session.commit()
    # a credit on a spend-y merchant is a REFUND, not SPEND
    _, _, tt = classify(db_session, "Swiggy refund", "credit", h.id)
    assert tt in ("REFUND", "INCOME", "TRANSFER", "PAYMENT") and tt != "SPEND"


def test_unseen_merchant_falls_back_to_keyword_rules(db_session):
    h = Household(name="H3"); db_session.add(h); db_session.commit()
    cat, conf, tt = classify(db_session, "Zzxq Unknown Merchant 99", "debit", h.id)
    assert cat == "Personal / Misc" and conf == "low"        # keyword fallback default
