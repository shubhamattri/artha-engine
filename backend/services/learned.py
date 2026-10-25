"""Learned merchant→category memory (the owner's idea: AI only on first sight).

Flow: classify a transaction → compute its `signature` (normalized merchant) → look it up here.
  • HIT  → reuse the stored category/merchant instantly (no AI).
  • MISS → AI classifies it once, and we SAVE the answer here for next time.
A user correcting a category writes a 'user' rule (highest trust) — so the same merchant is right
forever after one fix. Over time the store covers most spend and AI is rarely needed.
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from models.category_rule import CategoryRule

# strip the payment-rail prefix so "UPI-SALON ESSENTIAL-…", "UPI/SALON…", "Salon Essential" all
# reduce to the same signature.
_PREFIX = re.compile(r"\b(?:UPI|IMPS|NEFT|RTGS|ACH\s*[DC]|IB\s*FUNDS\s*TRANSFER|BIL|POS|ATM|NWD|MMT|TPT)\b", re.I)
_TRUST = {"user": 2, "ai": 1, "rule": 1}


def signature(text: str | None) -> str:
    """Stable key for a merchant/counterparty across statement/SMS/email wording."""
    raw = (text or "").strip()
    parts = _PREFIX.split(raw, maxsplit=1)
    s = (parts[1] if len(parts) > 1 else raw).strip(" -/:")
    s = re.split(r"[-/@]", s)[0]               # payee-name segment
    s = re.sub(r"[^A-Za-z ]", " ", s.upper())  # drop digits/handles/punctuation
    s = re.sub(r"\s+", " ", s).strip()
    return s[:32]


# NOTE: household_id is optional so the unattended background sync (ai_categorize/alert_ingest/
# sms_capture) can keep calling without a household — it then operates on the single household, as
# today. Callers SHOULD pass household_id once the ingest pipeline is household-aware, so the learned
# store becomes per-household.
def lookup(db: Session, sig: str, household_id: int | None = None) -> CategoryRule | None:
    if not sig:
        return None
    q = db.query(CategoryRule).filter(CategoryRule.signature == sig)
    if household_id is not None:
        q = q.filter(CategoryRule.household_id == household_id)
    return q.first()


def classify_text(db: Session, text: str | None, household_id: int | None = None) -> dict | None:
    """{'category','merchant'} for this merchant, or None if unseen. Precedence, highest first:

      1. what the USER explicitly taught for this merchant — nothing outranks an explicit decision;
      2. the canonical brand map (services.merchant_rules), objective for the brands it covers
         (Swiggy → Food, Blinkit → Groceries) and for generic patterns like any "… FUELS" pump;
      3. the AI-learned rule.

    The brand map used to sit at 1, so a category the user had explicitly set was ignored on every
    import and had to be corrected again. It still overrides AI guesses, which is what it is for.
    """
    from services import merchant_rules
    sig = signature(text)
    rule = lookup(db, sig, household_id)
    if rule is not None and rule.kind == "user":
        rule.hits = (rule.hits or 0) + 1
        return {"category": rule.category, "merchant": rule.merchant}
    canon = merchant_rules.resolve(text)
    if canon:
        return canon
    if rule is None:
        return None
    rule.hits = (rule.hits or 0) + 1
    return {"category": rule.category, "merchant": rule.merchant}


def remember(db: Session, sig: str, category: str, merchant: str | None, kind: str = "ai",
             household_id: int | None = None) -> None:
    """Upsert a rule. A higher-trust kind (user > ai) is never overwritten by a lower one."""
    if not sig or not category:
        return
    row = lookup(db, sig, household_id)
    if row is None:
        rule = CategoryRule(signature=sig, category=category, merchant=merchant, kind=kind, hits=0)
        if household_id is not None:
            rule.household_id = household_id
        db.add(rule)
        db.flush()  # autoflush is OFF → flush now so a later lookup in the SAME uncommitted batch
        return       # finds this row (two descriptions can normalize to one signature → else a dup-key crash)
    if _TRUST.get(kind, 1) >= _TRUST.get(row.kind, 1):
        row.category, row.kind = category, kind
        if merchant:
            row.merchant = merchant


def seed_from_transactions(db: Session) -> int:
    """Bootstrap the store from already-categorized transactions (most-common category per
    signature). Gives the classifier a head start so it rarely calls AI from day one."""
    from collections import Counter, defaultdict

    from models.transaction import Transaction
    from services.categories import is_variable

    buckets: dict[str, Counter] = defaultdict(Counter)
    names: dict[str, str] = {}
    for t in db.query(Transaction).filter(Transaction.merchant.isnot(None)).all():
        if not is_variable(t.category):
            continue
        sig = signature(t.merchant or t.description)
        if not sig:
            continue
        buckets[sig][t.category] += 1
        names.setdefault(sig, t.merchant)
    added = 0
    for sig, counts in buckets.items():
        if lookup(db, sig):
            continue
        category = counts.most_common(1)[0][0]
        db.add(CategoryRule(signature=sig, category=category, merchant=names.get(sig), kind="ai", hits=0))
        added += 1
    db.commit()
    return added
