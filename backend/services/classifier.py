"""THE one transaction classifier — the single path every domain uses (ingestion AND the recategorize
sweep), so a transaction gets the same category everywhere instead of two engines disagreeing.

Priority (highest trust first):
  1. merchant_rules   — canonical brands (Swiggy→Food, Blinkit→Groceries …), objective, authoritative
  2. learned store    — a user-confirmed or AI-learned rule for this merchant signature
  3. keyword rules    — parsers.categorizer (substring rules), the last-resort fallback
Then the direction decides the txn_type (a credit is income/refund/transfer, never SPEND).

Before this existed, `/transactions/recategorize` used ONLY the keyword rules — isolated from AI, the
learned store and merchant_rules — so re-running it flattened good categories into the fallback bucket.
"""

from __future__ import annotations

from sqlalchemy.orm import Session


def _txn_type(category: str, direction: str) -> str:
    from services.categories import CATEGORY_TYPE, Category
    if direction == "credit":
        if category in (Category.INCOME.value, Category.PERSONAL.value):
            return "INCOME"
        if category == Category.CC_PAYMENT.value:
            return "PAYMENT"
        if category == Category.INTERNAL_TRANSFER.value:
            return "TRANSFER"
        return "REFUND"
    return CATEGORY_TYPE.get(category, "SPEND")


def classify(db: Session, description: str | None, direction: str,
             household_id: int | None = None) -> tuple[str, str, str]:
    """(category, confidence, txn_type). Uses the learned store (which itself checks merchant_rules
    first), and only falls back to keyword rules when the merchant is genuinely unseen."""
    from parsers.categorizer import classify as keyword_classify
    from services import learned

    hit = learned.classify_text(db, description, household_id) if db is not None else None
    if hit and hit.get("category"):
        cat = hit["category"]
        return cat, "high", _txn_type(cat, direction)
    return keyword_classify(description, direction)   # keyword rules + its own txn_type logic
