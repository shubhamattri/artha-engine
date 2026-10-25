"""Transaction endpoints: statement upload + transaction listing."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.deps import get_current_household
from database import get_db
from models.tenant import Household
from models.transaction import Transaction
from services.ingest import ingest_statement, supported as supported_data

log = logging.getLogger(__name__)

router = APIRouter()


@router.post("/upload")
async def upload_statement(
    file: UploadFile = File(...),
    bank: str = Form(None),
    card_type: str = Form(None),
    card_last4: str = Form(None),
    db: Session = Depends(get_db),
    household: Household = Depends(get_current_household),
):
    """Parse an uploaded statement and insert new txns. Bank / card type / last-4 are
    AUTO-DETECTED when not supplied. Delegates to the shared pipeline (services.ingest)."""
    contents = await file.read()
    result = ingest_statement(
        db, file.filename or "upload", contents,
        bank=bank or None, card_type=card_type or None, card_last4=card_last4 or None,
        household_id=household.id,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=422, detail=result.get("error", "Could not ingest the file."))
    return result


@router.get("/supported")
def supported_formats():
    """Document exactly which banks and file types Artha can ingest."""
    return supported_data()


@router.post("/transactions/reconcile")
def reconcile(db: Session = Depends(get_db),
              household: Household = Depends(get_current_household)):
    """Pair & eliminate internal transfers between own bank accounts."""
    from services.reconcile import pair_internal_transfers
    return pair_internal_transfers(db, household_id=household.id)


@router.get("/reconcile/duplicates")
def preview_duplicates(db: Session = Depends(get_db),
                       household: Household = Depends(get_current_household)):
    """Read-only: cross-source / re-upload duplicate groups + how many rupees are double-counted."""
    from services import dedup
    return dedup.preview(db, household_id=household.id)


@router.post("/reconcile/duplicates")
def apply_duplicates(body: dict | None = None, db: Session = Depends(get_db),
                     household: Household = Depends(get_current_household)):
    """Merge duplicates into their primary (reversible). Default merges only HIGH-confidence
    clusters; pass {"all": true} to also merge the low-confidence ones you've reviewed."""
    from services import dedup
    only_high = not (body or {}).get("all", False)
    return dedup.apply_dedup(db, only_high=only_high, household_id=household.id)


@router.post("/reconcile/duplicates/undo")
def undo_duplicates(body: dict | None = None, db: Session = Depends(get_db),
                    household: Household = Depends(get_current_household)):
    """Restore merged duplicates back to active (a single primary's, or all)."""
    from services import dedup
    return dedup.undo(db, (body or {}).get("primary_id"), household_id=household.id)


@router.get("/transactions/duplicate-review")
def duplicate_review(db: Session = Depends(get_db),
                     household: Household = Depends(get_current_household)):
    """The AMBIGUOUS duplicate queue: same-card, same-amount, same-day, name-compatible pairs with no
    shared bank reference — genuinely uncertain, so a human confirms or dismisses each. Auto-merged and
    dismissed pairs never appear. Returns [{primary, duplicate, amount, why}]."""
    from services import dedup
    return {"candidates": dedup.review_candidates(db, household.id)}


@router.post("/transactions/duplicate-review/merge")
def duplicate_review_merge(body: dict, db: Session = Depends(get_db),
                           household: Household = Depends(get_current_household)):
    """Confirm a reviewed pair is one debit → merge it (reversible). Body: {primary_id, duplicate_id}."""
    from services import dedup
    b = body or {}
    try:
        return dedup.merge_pair(db, household.id, int(b["primary_id"]), int(b["duplicate_id"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=404, detail="Transaction not found") from exc


@router.post("/transactions/duplicate-review/dismiss")
def duplicate_review_dismiss(body: dict, db: Session = Depends(get_db),
                             household: Household = Depends(get_current_household)):
    """Mark a reviewed pair as NOT a duplicate so it never resurfaces. Body: {fp_a, fp_b}."""
    from services import dedup
    b = body or {}
    fp_a, fp_b = b.get("fp_a"), b.get("fp_b")
    if not fp_a or not fp_b:
        raise HTTPException(status_code=422, detail="fp_a and fp_b required")
    return dedup.dismiss_pair(db, household.id, fp_a, fp_b)


@router.post("/transactions/recategorize")
def recategorize_all(dry_run: bool = False, db: Session = Depends(get_db),
                     household: Household = Depends(get_current_household)):
    """Re-sweep categories using ONLY the trusted layers — canonical brands (merchant_rules) and the
    learned store (user/AI memory). It NEVER falls back to keyword guessing on an already-categorised
    row, so it can only correct (e.g. Swiggy→Food) — never downgrade a good AI category into
    'Personal / Misc' or turn a transfer into spend (which a keyword re-run would do). `dry_run=true`
    returns the diff without writing."""
    from services import learned
    from services.classifier import _txn_type
    rows = db.query(Transaction).filter(Transaction.household_id == household.id).all()
    changed = 0
    moves: dict[str, int] = {}
    for r in rows:
        hit = learned.classify_text(db, r.description, household.id)   # merchant_rules → learned only
        if not hit or not hit.get("category"):
            continue                                                  # unseen → leave the row untouched
        cat = hit["category"]
        tt = _txn_type(cat, r.direction)
        if (r.category, r.txn_type) != (cat, tt):
            moves[f"{r.category} → {cat}"] = moves.get(f"{r.category} → {cat}", 0) + 1
            changed += 1
            if not dry_run:
                r.category, r.confidence, r.txn_type = cat, "high", tt
    if not dry_run:
        db.commit()
    return {"total": len(rows), "reclassified": changed, "dry_run": dry_run,
            "moves": dict(sorted(moves.items(), key=lambda x: -x[1])[:20])}


@router.post("/transactions/normalize-merchants")
def normalize_merchants(dry_run: bool = False, db: Session = Depends(get_db),
                        household: Household = Depends(get_current_household)):
    """Make known brands consistent: recategorise every SPEND row to its canonical category (Swiggy/Bundl
    → Food, Blinkit → Groceries, Netflix → Entertainment …), never touching a merchant the user set.
    `dry_run=true` returns the diff without writing."""
    from services import merchant_rules
    return merchant_rules.apply(db, household.id, dry_run=dry_run)


@router.post("/transactions/normalize-banks")
def normalize_banks(dry_run: bool = False, db: Session = Depends(get_db),
                    household: Household = Depends(get_current_household)):
    """Rewrite garbled `bank` fields (e.g. 'Axis Select RuPay ••7788' → 'Axis') so billing cycles
    resolve correctly. Reproducible + idempotent; also runs on every sync. `dry_run` previews."""
    from services import accounts
    return accounts.repair_banks(db, household.id, dry_run=dry_run)


def _merchant_key(m: str | None) -> str:
    return " ".join((m or "").lower().split()[:2])


@router.get("/transactions/category-consistency")
def category_consistency(db: Session = Depends(get_db),
                         household: Household = Depends(get_current_household)):
    """Merchants whose SPEND lands in MORE THAN ONE category — the review queue. Each row shows the
    split so you can pick the right category once and fix every transaction for that merchant."""
    from collections import defaultdict
    rows = (db.query(Transaction)
            .filter(Transaction.household_id == household.id, Transaction.direction == "debit",
                    Transaction.txn_type == "SPEND", Transaction.archived.is_(False),
                    Transaction.dup_of_id.is_(None)).all())
    by: dict[str, dict] = defaultdict(lambda: {"display": "", "cats": defaultdict(lambda: [0, 0.0]), "total": 0.0})
    for r in rows:
        k = _merchant_key(r.merchant or r.description)
        if len(k) < 3:
            continue
        g = by[k]
        g["display"] = g["display"] or (r.merchant or r.description or k)[:40]
        g["cats"][r.category][0] += 1
        g["cats"][r.category][1] = round(g["cats"][r.category][1] + (r.amount or 0.0), 2)
        g["total"] = round(g["total"] + (r.amount or 0.0), 2)
    out = []
    for k, g in by.items():
        if len(g["cats"]) < 2:
            continue
        cats = [{"category": c, "count": n, "amount": a}
                for c, (n, a) in sorted(g["cats"].items(), key=lambda x: -x[1][1])]
        out.append({"key": k, "display": g["display"], "total": g["total"], "categories": cats})
    out.sort(key=lambda x: -x["total"])
    return {"merchants": out}


class MerchantCatIn(BaseModel):
    key: str
    category: str


@router.post("/transactions/merchant-category")
def set_merchant_category(body: MerchantCatIn, db: Session = Depends(get_db),
                          household: Household = Depends(get_current_household)):
    """Pin a merchant to ONE category: recategorise every SPEND txn for that merchant and remember it as
    a USER rule for each of its signatures, so future transactions stay consistent."""
    from models.category_rule import CategoryRule
    from services import learned
    from services.audit import log_event
    from services.categories import CATEGORY_TYPE
    rows = (db.query(Transaction)
            .filter(Transaction.household_id == household.id, Transaction.direction == "debit",
                    Transaction.txn_type == "SPEND", Transaction.archived.is_(False)).all())
    tt = CATEGORY_TYPE.get(body.category, "SPEND")
    changed = 0
    sigs: set[str] = set()
    for r in rows:
        if _merchant_key(r.merchant or r.description) != body.key:
            continue
        sigs.add(learned.signature(r.merchant or r.description))
        if r.category != body.category:
            log_event(db, household_id=r.household_id, txn_id=r.id, action="recategorize",
                      field="category", old=r.category, new=body.category, actor="merchant-review")
            r.category, r.confidence, r.txn_type = body.category, "high", tt
            changed += 1
    for sig in sigs:                                    # remember as USER rule so it sticks
        rule = db.query(CategoryRule).filter(CategoryRule.household_id == household.id,
                                             CategoryRule.signature == sig).first()
        if rule:
            rule.category, rule.kind = body.category, "user"
        elif sig:
            db.add(CategoryRule(household_id=household.id, signature=sig,
                                category=body.category, kind="user"))
    db.commit()
    return {"changed": changed, "category": body.category, "signatures": len(sigs)}


@router.post("/transactions/quick")
def quick_add(body: dict, db: Session = Depends(get_db),
              household: Household = Depends(get_current_household)):
    """Log a spend in seconds (the daily driver). Creates a MANUAL transaction — clean data the user
    typed, used for cycle-to-date tracking + the spend-aware afford check (statements stay the later
    reconciliation truth)."""
    import hashlib
    import uuid
    from datetime import date as _date

    amount = float(body.get("amount") or 0)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
    when = (body.get("date") or _date.today().isoformat())[:10]
    note = (body.get("note") or body.get("merchant") or "Cash spend").strip()
    # "Others" was dropped in the 21->18 merge and survives only as a MERGE key, so writing it puts
    # the row in a bucket no picker, budget or query recognises.
    from services.categories import Category
    category = body.get("category") or Category.PERSONAL.value
    account = (body.get("account") or "Cash").strip()

    t = Transaction(
        household_id=household.id,  # belongs to the household that logged it
        fingerprint=hashlib.sha1(f"manual:{uuid.uuid4()}".encode()).hexdigest(),
        date=when, description=note, merchant=note, amount=amount,
        direction="debit", category=category, confidence="high", txn_type="SPEND",
        bank=account, card_type="Manual", card_last4="0000", stmt_month=when[:7],
        source="manual",
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    from services.audit import log_event
    log_event(db, household_id=t.household_id, txn_id=t.id, action="create",
              field="amount", old=None, new=t.amount, actor="user")
    db.commit()
    return {"ok": True, "id": t.id, "date": t.date, "merchant": t.merchant,
            "amount": t.amount, "category": t.category}


@router.get("/transactions/categories")
def transaction_categories(db: Session = Depends(get_db),
                           household: Household = Depends(get_current_household)):
    """Canonical category list (single source of truth) + whether each is an everyday spend bucket.
    Frontend dropdowns read this: the spend picker shows is_spend=true; edit shows all. Includes this
    household's user-defined budget categories (custom=true, always is_spend) so they appear in every
    picker automatically."""
    from services.categories import (
        Category, category_settings, custom_category_names, is_variable,
    )
    # Archived categories keep every past transaction that already points at them, and stay out of the
    # list you can file NEW spend into. That is the whole difference between archiving and deleting:
    # one stops offering a category, the other rewrites where its money went.
    settings = category_settings(db, household.id)

    def _archived(name: str) -> bool:
        return settings.get(name, {}).get("active", True) is False

    def _label(name: str) -> str:
        return settings.get(name, {}).get("display_name") or name

    cats = [{"value": c.value, "label": _label(c.value),
             "is_spend": is_variable(c.value, db, household.id), "custom": False}
            for c in Category if not _archived(c.value)]
    cats += [{"value": name, "label": _label(name),
              "is_spend": is_variable(name, db, household.id), "custom": True}
             for name in custom_category_names(db, household.id) if not _archived(name)]
    return {"categories": cats}


@router.patch("/transactions/{txn_id}")
def update_transaction(txn_id: int, body: dict, db: Session = Depends(get_db),
                       household: Household = Depends(get_current_household)):
    """Manually correct a single transaction (the user is the final authority on their data).
    Editable: amount, date, merchant, description, direction, account, category, txn_type.
    Changing the category re-derives txn_type from the canonical map so the Plan/afford update
    immediately (e.g. a CC bill payment mis-filed as a spend stops inflating the budget)."""
    from services.categories import CATEGORY_TYPE

    # Scope by household — you can only ever edit your own ledger, never another's by id.
    t = (db.query(Transaction)
         .filter(Transaction.id == txn_id, Transaction.household_id == household.id).first())
    if not t:
        raise HTTPException(status_code=404, detail="Transaction not found")

    # record every field's old→new as an immutable audit event (the substrate for history/undo/learning)
    changes: list[tuple[str, object, object]] = []
    if body.get("amount") is not None:
        amt = float(body["amount"])
        if amt <= 0:
            raise HTTPException(status_code=400, detail="Amount must be positive")
        changes.append(("amount", t.amount, amt)); t.amount = amt
    if body.get("date"):
        changes.append(("date", t.date, body["date"][:10]))
        t.date = body["date"][:10]; t.stmt_month = body["date"][:7]
    if "merchant" in body:
        new_m = (body["merchant"] or "").strip() or None
        changes.append(("merchant", t.merchant, new_m)); t.merchant = new_m
    if body.get("description"):
        changes.append(("description", t.description, body["description"].strip()))
        t.description = body["description"].strip()
    if body.get("direction") in ("debit", "credit"):
        changes.append(("direction", t.direction, body["direction"])); t.direction = body["direction"]
    if body.get("account"):
        changes.append(("account", t.bank, body["account"].strip())); t.bank = body["account"].strip()
    if body.get("category"):
        changes.append(("category", t.category, body["category"]))
        t.category = body["category"]
        t.confidence = "high"  # user-confirmed
        if t.direction == "debit":  # re-derive spend type from the canonical taxonomy
            t.txn_type = CATEGORY_TYPE.get(body["category"], "SPEND")
        # teach the learned store: this merchant → this category, forever (highest trust)
        from services.learned import remember, signature
        remember(db, signature(t.merchant or t.description), body["category"], t.merchant, "user")
    if body.get("txn_type"):  # explicit override always wins
        changes.append(("txn_type", t.txn_type, body["txn_type"])); t.txn_type = body["txn_type"]

    from services.audit import log_edits
    log_edits(db, household_id=household.id, txn_id=t.id, changes=changes, actor="user")
    db.commit()
    db.refresh(t)
    return {"ok": True, "id": t.id, "date": t.date, "merchant": t.merchant, "amount": t.amount,
            "direction": t.direction, "category": t.category, "txn_type": t.txn_type, "bank": t.bank}


@router.delete("/transactions/{txn_id}")
def delete_transaction(txn_id: int, hard: bool = False, db: Session = Depends(get_db),
                       household: Household = Depends(get_current_household)):
    """Remove a transaction. Default = ARCHIVE (soft: kept for history, excluded from totals,
    restorable). `?hard=true` = permanently DELETE the row (for a genuinely wrong entry, e.g. a
    mistyped manual log). To merely drop something from spend, reclassify it to a non-spend category."""
    # Scope by household — you can only ever delete your own ledger, never another's by id.
    t = (db.query(Transaction)
         .filter(Transaction.id == txn_id, Transaction.household_id == household.id).first())
    if not t:
        raise HTTPException(status_code=404, detail="Transaction not found")
    from services.audit import log_event
    if hard:
        from models.transaction import DeletedFingerprint
        # tombstone the fingerprint so the 3-hourly sync never re-creates this row
        # The tombstone is keyed (household, fingerprint): deleting a row here must never suppress
        # another household's identical transaction.
        if t.fingerprint and not db.query(DeletedFingerprint).get((household.id, t.fingerprint)):
            db.add(DeletedFingerprint(household_id=household.id, fingerprint=t.fingerprint))
        # log BEFORE the row is gone, capturing what it was (audit survives the delete — no FK on txn_id)
        log_event(db, household_id=household.id, txn_id=txn_id, action="delete",
                  field="merchant", old=t.merchant or t.description, new=None, actor="user")
        db.delete(t)
        db.commit()
        return {"ok": True, "deleted": txn_id}
    t.archived = True
    log_event(db, household_id=household.id, txn_id=txn_id, action="archive",
              field="archived", old=False, new=True, actor="user")
    db.commit()
    return {"ok": True, "archived": txn_id}


@router.post("/transactions/sync-gmail-alerts")
def sync_gmail_alerts(days: int = 14, db: Session = Depends(get_db),
                      household: Household = Depends(get_current_household)):
    """Pull bank transaction-ALERT emails (per-spend, no attachment) → transactions. The no-upload
    daily capture: covers every merchant via the bank's own alerts. Dedups by email id."""
    from services.alert_ingest import sync_alerts
    return sync_alerts(db, days=days, household_id=household.id)


@router.post("/transactions/sync-sms")
def sync_sms_endpoint(days: int = 30, db: Session = Depends(get_db),
                      household: Household = Depends(get_current_household)):
    """Capture bank transaction SMS forwarded to this Mac's Messages (100% coverage, all-Apple household).
    Needs Text Message Forwarding ON + Full Disk Access. No-op + clear reason if not set up."""
    from services.sms_capture import sync_sms
    return sync_sms(db, since_days=days)


@router.post("/transactions/sync")
def sync_all(days: int = 4, db: Session = Depends(get_db),
             household: Household = Depends(get_current_household)):
    """ONE-button sync: pull SMS + Gmail bank alerts, AI-categorise the new rows, and auto-merge
    duplicates (each capturer dedups on insert). Returns one combined summary for the UI's Sync
    button — so the user clicks once and both channels come in, clean."""
    from services.alert_ingest import sync_alerts
    from services.sms_capture import sync_sms

    out: dict = {}
    try:
        out["sms"] = sync_sms(db, since_days=days, household_id=household.id)
    except Exception as e:  # noqa: BLE001 — surface a per-channel failure, never fail the whole sync
        out["sms"] = {"ok": False, "error": str(e)}
    try:
        out["gmail"] = sync_alerts(db, days=days, household_id=household.id)
    except Exception as e:  # noqa: BLE001
        out["gmail"] = {"ok": False, "error": str(e)}
    out["categorize"] = run_categorize(db, limit=200, household_id=household.id)
    # SELF-HEAL every sync so categories stay consistent without a manual button:
    #  1) canonical brands (Swiggy/Bundl→Food, Blinkit→Groceries …) — corrects any that slipped;
    #  2) confirmed identity links (maid/cook/rent/EMI → their committed bucket, out of variable spend),
    #     AFTER the AI pass so it always wins.
    from services import accounts, bank_ref, commitments, dedup, merchant_rules

    def step(name: str, fn):
        """Run one self-heal step; record a failure and keep going.

        The two channels above have always degraded this way and the eight steps below did not, so a
        single broken one answered 500 and the Sync button did nothing at all — which is how a
        missing argument in `auto_merge` took the whole button out. A sync that completes eight of
        nine jobs and says so is strictly better than one that reports failure for all nine.
        """
        try:
            out[name] = fn()
        except Exception as e:  # noqa: BLE001
            log.exception("sync step %s failed", name)
            out[name] = {"ok": False, "error": str(e)}
            failed.append(name)

    failed: list[str] = []
    step("bank_norm", lambda: accounts.repair_banks(db, household.id))  # garbled bank → right cycle
    # Re-attribute rows stuck on a placeholder last4 by reading their own stored text. The resolver
    # used to run only at ingest, so anything it missed then stayed unattributed forever even when
    # the description named the account outright.
    step("reattributed", lambda: accounts.repair_unattributed(db, household.id))
    step("merchant_norm", lambda: merchant_rules.apply(db, household.id))
    step("relink", lambda: commitments.apply_link_categories(db, household.id))
    # A debit equal to a tracked loan's EMI is that EMI, whatever the bank wrote in the narration.
    step("loan_emis", lambda: commitments.tag_loan_emis(db, household.id))
    # Extract the deterministic bank reference for any new row, THEN run one global merge sweep — so the
    # manual Sync button leaves the ledger as clean as the scheduled job (not only per-capturer on insert).
    step("refs", lambda: bank_ref.backfill(db, household.id))
    step("dedup", lambda: dedup.auto_merge(db, household_id=household.id))
    # Pair debit<->credit across the household's OWN accounts and tag both as internal transfers, so
    # moving your own money between your own accounts never lands in the spending budget. This existed
    # only behind a manual POST /transactions/reconcile, which meant a transfer made today stayed
    # counted as real spend until somebody happened to press it.
    from services.reconcile import pair_internal_transfers
    step("transfers", lambda: pair_internal_transfers(db, household_id=household.id))
    new = (out["sms"].get("added", 0) or 0) + (out["gmail"].get("added", 0) or 0)
    merged = (out["dedup"].get("merged_rows", 0) or 0)
    # `failed` is published, not swallowed: a sync that quietly skipped the duplicate merge and then
    # reported "Up to date" is worse than one that says which part did not run.
    out["summary"] = {"new": new, "merged": merged, "failed": failed}
    return out


def run_categorize(db: Session, limit: int = 400, household_id: int | None = None) -> dict:
    """AI pass: clean merchant name + refine category for unprocessed transactions (merchant IS NULL).
    Plain helper so the unattended nightly job (daily_sync.py) can call it WITHOUT a request/household
    context — household_id=None = all (single-household-safe); the route passes the request's household.
    Only refines discretionary SPEND; never re-touches a user-confirmed row (confidence='high')."""
    from services import ai_categorize
    from services.llm import available

    if not available():
        return {"ok": False, "reason": "AI not configured (no ANTHROPIC_API_KEY)", "updated": 0}

    dq = db.query(Transaction.description).filter(
        Transaction.merchant.is_(None), Transaction.confidence != "high")
    if household_id is not None:
        dq = dq.filter(Transaction.household_id == household_id)
    distinct = [d[0] for d in dq.distinct().limit(limit).all()]
    if not distinct:
        return {"ok": True, "distinct": 0, "updated": 0, "note": "nothing left to categorize"}

    mapping = ai_categorize.categorize(distinct, db=db)
    if not mapping:
        return {"ok": False, "reason": "AI returned nothing", "updated": 0}

    from services.categories import CATEGORY_TYPE
    updated = 0
    for desc, info in mapping.items():
        rq = db.query(Transaction).filter(Transaction.description == desc)
        if household_id is not None:
            rq = rq.filter(Transaction.household_id == household_id)
        for r in rq.all():
            if r.confidence == "high":   # user-confirmed → leave it exactly as the user set it
                continue
            r.merchant = info.get("merchant") or r.merchant
            # Re-classify only rows currently treated as SPEND/FEE. CRUCIAL: also re-derive txn_type
            # from the category, so a row the AI now recognises as a person-transfer or a loan
            # (Internal Transfer / Loan EMI) actually LEAVES the spend total instead of inflating it.
            if info.get("category") and r.txn_type in ("SPEND", "FEE"):
                old_category = r.category  # capture BEFORE reassigning, for the audit trail
                r.category = info["category"]
                r.txn_type = CATEGORY_TYPE.get(info["category"], "SPEND")
                if old_category != r.category:  # only log a genuine change
                    from services.audit import log_event
                    log_event(db, household_id=r.household_id, txn_id=r.id, action="categorize",
                              field="category", old=old_category, new=r.category, actor="ai")
            updated += 1
    db.commit()
    return {"ok": True, "distinct": len(distinct), "categorized": len(mapping), "rows_updated": updated}


@router.post("/transactions/categorize-ai")
def categorize_ai(limit: int = 400, db: Session = Depends(get_db),
                  household: Household = Depends(get_current_household)):
    """AI categorize pass, scoped to the current household (see run_categorize)."""
    return run_categorize(db, limit, household.id)


@router.get("/transactions/facets")
def transaction_facets(db: Session = Depends(get_db),
                       household: Household = Depends(get_current_household)):
    """Distinct values for the filter dropdowns (months, types, cards) — scoped to the household."""
    hh = Transaction.household_id == household.id
    months = [
        m[0] for m in db.query(Transaction.stmt_month).filter(hh)
        .distinct().order_by(Transaction.stmt_month.desc()).all() if m[0]
    ]
    types = [
        t[0] for t in db.query(Transaction.txn_type).filter(hh).distinct().all() if t[0]
    ]
    # ONE clean entry per physical card, keyed by last-4 (not the per-source bank spelling),
    # so the same card never shows up as "HDFC" + "Hdfcbank Alert" + "SMS". See services/accounts.
    from services.accounts import card_list
    return {"months": months, "types": sorted(types),
            "cards": card_list(db, household_id=household.id)}


@router.get("/transactions")
def list_transactions(
    month: str | None = None,
    category: str | None = None,
    bank: str | None = None,
    txn_type: str | None = None,
    card_last4: str | None = None,
    include_dups: bool = False,
    cycle: str | None = None,
    ids: str | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
    household: Household = Depends(get_current_household),
):
    """List transactions with optional filters, newest first. Hides merged duplicates by default.
    cycle='current' → only this per-card billing cycle's spend (the 12th→ / 22nd→ windows).
    ids='1,2,3' → exactly those rows (exact drill-down for a category×week cell).
    start/end='YYYY-MM-DD' → an inclusive date window (the general historical-range primitive)."""
    # Tenant scope FIRST — a household only ever sees its own ledger (BUILD-LOG M1 Slice 1).
    query = (
        db.query(Transaction)
        .filter(Transaction.household_id == household.id)
        .filter(Transaction.archived.is_(False))
    )

    if ids is not None:
        # Exact set (from a category×week cell). Supersedes every other window filter; still tenant-scoped.
        wanted = [int(x) for x in ids.split(",") if x.strip().lstrip("-").isdigit()]
        query = query.filter(Transaction.id.in_(wanted or [-1]))
        cycle = month = None
    if not include_dups:
        query = query.filter(Transaction.dup_of_id.is_(None))
    if cycle:
        # cycle='current' → the live billing cycle; cycle='YYYY-MM' → the cycle whose start-day falls in
        # that month (lets Spending step through previous cycles, like Plan). Per-card windows, so it
        # matches Plan's cycle totals.
        from datetime import date as _date, timedelta
        from services.cycle import current_cycle_rows
        if cycle == "current":
            anchor = _date.today()
        else:
            from calendar import monthrange

            from api.budget import settings_cycle_start
            from services.cycle import cycle_bounds
            y, mo = map(int, cycle.split("-"))
            sd = settings_cycle_start(db, household.id)
            _, cend = cycle_bounds(_date(y, mo, min(sd, monthrange(y, mo)[1])), sd)
            anchor = cend - timedelta(days=1)
        rows = [r.id for r in current_cycle_rows(db, anchor, household_id=household.id)]
        query = query.filter(Transaction.id.in_(rows or [-1]))
        month = None  # cycle mode supersedes the calendar-month filter
    if month:
        query = query.filter(Transaction.stmt_month == month)
    if category:
        query = query.filter(Transaction.category == category)
    if bank:
        query = query.filter(func.lower(Transaction.bank) == bank.lower())
    if txn_type:
        query = query.filter(Transaction.txn_type == txn_type)
    if card_last4:
        query = query.filter(Transaction.card_last4 == card_last4)
    if start:
        query = query.filter(Transaction.date >= start)   # inclusive date window (ISO dates sort lexically)
    if end:
        query = query.filter(Transaction.date <= end)

    total = query.count()

    rows = (
        query.order_by(Transaction.date.desc(), Transaction.id.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )

    transactions = [
        {
            "id": r.id,
            "statement_id": r.statement_id,
            "date": r.date,
            "description": r.description,
            "merchant": r.merchant,
            "amount": r.amount,
            "direction": r.direction,
            "category": r.category,
            "confidence": r.confidence,
            "txn_type": r.txn_type,
            "bank": r.bank,
            "card_type": r.card_type,
            "card_last4": r.card_last4,
            "stmt_month": r.stmt_month,
        }
        for r in rows
    ]

    return {"total": total, "transactions": transactions}
