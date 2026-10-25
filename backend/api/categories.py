"""Household-defined budget categories — create / rename / delete from the Plan UI.

These extend the built-in taxonomy (services.categories) with the household's own everyday-spend
buckets. A custom category is a variable spend category everywhere at once because the readers merge it
in (services.categories.variable_category_values, money_slice._variable_set, the /transactions/categories
picker, the Plan). Rename and delete rewrite the name across the household's existing data so nothing is
left pointing at a stale category (delete reassigns to 'Personal / Misc'; the decision, 2026-07-25).
"""

from api.deps import get_current_household
from database import get_db
from fastapi import APIRouter, Body, Depends, HTTPException
from models.category_rule import CategoryRule
from models.custom_category import CustomCategory
from models.household import Budget
from models.tenant import Household
from models.transaction import Transaction
from services.categories import _VALUES, Category
from sqlalchemy.orm import Session

router = APIRouter()

_PERSONAL = Category.PERSONAL.value   # "Personal / Misc" — where a deleted category's data lands
_MAX_LEN = 40


def _clean_name(raw: str | None) -> str:
    name = (raw or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Category name can't be empty")
    if len(name) > _MAX_LEN:
        raise HTTPException(status_code=400, detail=f"Keep it under {_MAX_LEN} characters")
    lower = name.lower()
    if any(lower == b.lower() for b in _VALUES):
        raise HTTPException(status_code=409, detail=f"“{name}” is already a built-in category")
    return name


def _serialize(c: CustomCategory) -> dict:
    # Identity only. How a category behaves or looks comes from /categories/all, which reads
    # category_settings and answers for built-ins too.
    return {"id": c.id, "name": c.name, "sort_order": c.sort_order}


def _find(db: Session, cat_id: int, household_id: int) -> CustomCategory:
    row = (db.query(CustomCategory)
           .filter(CustomCategory.id == cat_id, CustomCategory.household_id == household_id).first())
    if not row:
        raise HTTPException(status_code=404, detail="Category not found")
    return row


def _resolve_target(db: Session, household_id: int, into: str | None, exclude_id: int) -> str:
    """Where a retired category's history goes. A name, built-in or custom, that is not the category
    being retired. Defaults to Personal / Misc, which is what delete always did."""
    if not into:
        return _PERSONAL
    target = into.strip()
    if any(target.lower() == b.lower() for b in _VALUES):
        return next(b for b in _VALUES if b.lower() == target.lower())
    row = (db.query(CustomCategory)
           .filter(CustomCategory.household_id == household_id, CustomCategory.id != exclude_id)
           .filter(CustomCategory.name.ilike(target)).first())
    if not row:
        raise HTTPException(status_code=400, detail=f"No category named “{target}” to move this into")
    return row.name


@router.post("/categories")
def create_category(body: dict = Body(...), db: Session = Depends(get_db),
                    household: Household = Depends(get_current_household)):
    """Add a budget category. It shows up in every picker and on the Plan immediately."""
    name = _clean_name(body.get("name"))
    exists = (db.query(CustomCategory)
              .filter(CustomCategory.household_id == household.id)
              .filter(CustomCategory.name.ilike(name)).first())
    if exists:
        raise HTTPException(status_code=409, detail=f"“{name}” already exists")
    last = (db.query(CustomCategory)
            .filter(CustomCategory.household_id == household.id)
            .order_by(CustomCategory.sort_order.desc()).first())
    row = CustomCategory(household_id=household.id, name=name,
                         sort_order=(last.sort_order + 1) if last else 0)
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"ok": True, **_serialize(row)}


@router.patch("/categories/{cat_id}")
def rename_category(cat_id: int, body: dict = Body(...), db: Session = Depends(get_db),
                    household: Household = Depends(get_current_household)):
    """Rename a custom category and rewrite the new name across this household's transactions, budget
    targets and learned merchant rules — so every screen stays consistent, no orphaned old name."""
    row = (db.query(CustomCategory)
           .filter(CustomCategory.id == cat_id, CustomCategory.household_id == household.id).first())
    if not row:
        raise HTTPException(status_code=404, detail="Category not found")
    new_name = _clean_name(body.get("name"))
    old_name = row.name
    if new_name == old_name:
        return {"ok": True, **_serialize(row)}
    clash = (db.query(CustomCategory)
             .filter(CustomCategory.household_id == household.id, CustomCategory.id != cat_id)
             .filter(CustomCategory.name.ilike(new_name)).first())
    if clash:
        raise HTTPException(status_code=409, detail=f"“{new_name}” already exists")
    row.name = new_name
    _rewrite_category(db, household.id, old_name, new_name)
    db.commit()
    db.refresh(row)
    return {"ok": True, **_serialize(row)}


@router.post("/categories/{cat_id}/merge")
def merge_category(cat_id: int, body: dict = Body(...), db: Session = Depends(get_db),
                   household: Household = Depends(get_current_household)):
    """Fold one category into another: every transaction, budget target and learned merchant rule moves,
    then the source is removed. This is what "Food" and "Food & Dining" both existing needs.

    Budget targets for the same month are ADDED rather than one silently winning, because two targets
    for what turns out to be one category were always meant to be one number."""
    row = _find(db, cat_id, household.id)
    target = _resolve_target(db, household.id, body.get("into"), exclude_id=cat_id)
    if target == row.name:
        raise HTTPException(status_code=400, detail="A category can’t merge into itself")
    moved = _rewrite_category(db, household.id, row.name, target, merge_budgets=True)
    db.delete(row)
    db.commit()
    return {"ok": True, "merged_into": target, **moved}


@router.delete("/categories/{cat_id}")
def delete_category(cat_id: int, into: str | None = None, db: Session = Depends(get_db),
                    household: Household = Depends(get_current_household)):
    """Delete a custom category. Its transactions, budget targets and learned rules move to `into`
    (default 'Personal / Misc') so nothing is orphaned and past spend stays counted.

    Prefer archiving when the category was real and simply stopped being used: this moves history into
    another bucket, which says the money went somewhere it did not."""
    row = (db.query(CustomCategory)
           .filter(CustomCategory.id == cat_id, CustomCategory.household_id == household.id).first())
    if not row:
        raise HTTPException(status_code=404, detail="Category not found")
    target = _resolve_target(db, household.id, into, exclude_id=cat_id)
    moved = _rewrite_category(db, household.id, row.name, target, merge_budgets=True)
    db.delete(row)
    db.commit()
    return {"ok": True, "reassigned_to": target, **moved}


@router.get("/categories/{cat_id}/rules")
def category_rules(cat_id: int, db: Session = Depends(get_db),
                   household: Household = Depends(get_current_household)):
    """Which merchants land in this category, and why.

    Categorisation is otherwise a black box: a transaction appears under a heading and nothing on any
    screen says whether that came from something you confirmed, something the model guessed, or a
    built-in rule. `kind` is that answer, and `hits` is how much traffic each rule carries, so the ones
    worth correcting sort to the top.
    """
    row = _find(db, cat_id, household.id)
    rules = (db.query(CategoryRule)
             .filter(CategoryRule.household_id == household.id, CategoryRule.category == row.name)
             .order_by(CategoryRule.hits.desc(), CategoryRule.id)
             .all())
    return {
        "category": row.name,
        "rules": [{"id": r.id, "signature": r.signature, "merchant": r.merchant,
                   "kind": r.kind, "hits": r.hits} for r in rules],
    }


@router.post("/categories/{cat_id}/split")
def split_category(cat_id: int, body: dict = Body(...), db: Session = Depends(get_db),
                   household: Household = Depends(get_current_household)):
    """Pull selected merchants out of a category into a new one, history included.

    The alternative was to create a category and re-file transactions by hand, which nobody does, so
    "Personal / Misc" becomes the bucket everything unclear falls into and stops meaning anything. This
    moves the chosen merchants' rules AND their past transactions, so the new category is populated the
    moment it exists rather than starting empty and filling up only from future spend.
    """
    row = _find(db, cat_id, household.id)
    signatures = body.get("signatures") or []
    if not isinstance(signatures, list) or not signatures:
        raise HTTPException(status_code=400, detail="Pick at least one merchant to split out")
    new_name = _clean_name(body.get("name"))
    if (db.query(CustomCategory)
            .filter(CustomCategory.household_id == household.id)
            .filter(CustomCategory.name.ilike(new_name)).first()):
        raise HTTPException(status_code=409, detail=f"“{new_name}” already exists")

    rules = (db.query(CategoryRule)
             .filter(CategoryRule.household_id == household.id,
                     CategoryRule.category == row.name,
                     CategoryRule.signature.in_(signatures))
             .all())
    if not rules:
        raise HTTPException(status_code=400, detail="None of those merchants are in this category")

    last = (db.query(CustomCategory)
            .filter(CustomCategory.household_id == household.id)
            .order_by(CustomCategory.sort_order.desc()).first())
    created = CustomCategory(household_id=household.id, name=new_name,
                             sort_order=(last.sort_order + 1) if last else 0)
    db.add(created)
    db.flush()
    # Carry the source's "committed" answer onto the new category. Splitting "School fees" must not
    # produce an everyday-spend bucket the Plan starts judging against a weekly pace.
    from models.category_settings import CategorySettings
    from services.categories import category_settings as _settings
    src_variable = _settings(db, household.id).get(row.name, {}).get("is_variable")
    if src_variable is not None:
        db.add(CategorySettings(household_id=household.id, category=new_name,
                                is_variable=src_variable))

    # The rules move, and so does the history behind them. Matching on merchant rather than on the
    # rule's signature because that is what a Transaction actually carries; a rule with no merchant
    # recorded can only move itself, and its past rows stay where they are rather than being guessed at.
    merchants = {r.merchant for r in rules if r.merchant}
    moved_txns = 0
    if merchants:
        moved_txns = (db.query(Transaction)
                      .filter(Transaction.household_id == household.id,
                              Transaction.category == row.name,
                              Transaction.merchant.in_(merchants))
                      .update({Transaction.category: new_name}, synchronize_session=False))
    for r in rules:
        r.category = new_name
    db.commit()
    db.refresh(created)
    return {"ok": True, "split_from": row.name, "rules_moved": len(rules),
            "transactions_moved": moved_txns, **_serialize(created)}


@router.get("/categories/all")
def list_all_categories(db: Session = Depends(get_db),
                        household: Household = Depends(get_current_household)):
    """EVERY category this household can manage, built-in and its own, in one list.

    /categories returns only the household's own additions, which is what the Plan's picker wants but
    is the wrong list to manage: a household using only built-ins saw an empty management screen while
    Groceries and Travel sat on the Plan behind it.
    """
    from services.categories import all_manageable_categories
    return {"categories": all_manageable_categories(db, household.id)}


@router.patch("/categories/by-name/{name}/properties")
def update_category_settings(name: str, body: dict = Body(...), db: Session = Depends(get_db),
                             household: Household = Depends(get_current_household)):
    """Set colour / target / archive / committed / label on ANY category, built-in included.

    `display_name` is a LABEL, never a rewrite. Forty places compare against Category.*.value and the
    AI classifier's prompt embeds the names as text, so moving a built-in's stored name would drop it
    out of the Plan pool and change what the model is told. The key stays, the household sees its own
    word for it. A custom category has no such constraint and keeps its real rename on /categories/{id}.
    """
    from models.category_settings import CategorySettings
    from services.categories import all_manageable_categories

    known = {c["category"] for c in all_manageable_categories(db, household.id)}
    if name not in known:
        raise HTTPException(status_code=404, detail=f"No category named “{name}”")

    row = (db.query(CategorySettings)
           .filter(CategorySettings.household_id == household.id,
                   CategorySettings.category == name).first())
    if row is None:
        row = CategorySettings(household_id=household.id, category=name)
        db.add(row)

    if "display_name" in body:
        label = (body.get("display_name") or "").strip()
        if len(label) > _MAX_LEN:
            raise HTTPException(status_code=400, detail=f"Keep it under {_MAX_LEN} characters")
        # Blank clears the override and the canonical name shows again, so a rename is always undoable.
        row.display_name = label or None
    if "color" in body:
        colour = (body.get("color") or "").strip() or None
        if colour and len(colour) > 32:
            raise HTTPException(status_code=400, detail="That doesn’t look like a colour")
        row.color = colour
    if "active" in body:
        row.active = bool(body["active"])
    if "is_variable" in body:
        # None puts it back to whatever the taxonomy says, which is different from setting it True.
        row.is_variable = None if body["is_variable"] is None else bool(body["is_variable"])
    if "default_target_inr" in body:
        raw = body.get("default_target_inr")
        if raw in (None, ""):
            row.default_target_inr = None
            _set_month_target(db, household.id, name, None)
        else:
            try:
                val = float(raw)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="Target must be a number") from None
            if val < 0:
                raise HTTPException(status_code=400, detail="A target can’t be negative")
            row.default_target_inr = round(val, 2)
            # Also set THIS month, because that is the number the person is looking at. Writing only
            # the default meant typing 40,000 here changed nothing on the Plan until next month, and
            # the field would still have read "no target" right after being saved.
            _set_month_target(db, household.id, name, round(val, 2))
    db.commit()

    current = next(c for c in all_manageable_categories(db, household.id) if c["category"] == name)
    return {"ok": True, **current}


@router.post("/categories/order")
def order_all_categories(body: dict = Body(...), db: Session = Depends(get_db),
                         household: Household = Depends(get_current_household)):
    """Display order across the whole list, built-ins included, by canonical name."""
    from models.category_settings import CategorySettings
    from services.categories import all_manageable_categories

    names = body.get("names") or []
    if not isinstance(names, list):
        raise HTTPException(status_code=400, detail="names must be a list")
    known = {c["category"] for c in all_manageable_categories(db, household.id)}
    unknown = [n for n in names if n not in known]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown categories: {unknown}")

    existing = {r.category: r for r in db.query(CategorySettings)
                .filter(CategorySettings.household_id == household.id).all()}
    for i, n in enumerate(names):
        row = existing.get(n)
        if row is None:
            row = CategorySettings(household_id=household.id, category=n)
            db.add(row)
        row.sort_order = i
    db.commit()
    return {"ok": True, "ordered": len(names)}


def _set_month_target(db: Session, household_id: int, category: str, value: float | None) -> None:
    """Set (or clear) the CURRENT month's budget row for a category.

    A category's target lives per month in `budgets`; `default_target_inr` only seeds months that have
    no row yet. Editing a target from the category screen has to touch the month you are actually in,
    or the Plan goes on showing the old figure and the edit looks like it did nothing.
    """
    from datetime import date

    from models.household import Budget
    month = date.today().strftime("%Y-%m")
    row = (db.query(Budget)
           .filter(Budget.household_id == household_id, Budget.category == category,
                   Budget.month == month).first())
    if value is None:
        if row is not None:
            db.delete(row)
        return
    if row is None:
        db.add(Budget(household_id=household_id, category=category, month=month,
                      monthly_limit_inr=value))  # typed here, so source stays NULL
    else:
        row.monthly_limit_inr = value


def _rewrite_category(db: Session, household_id: int, old: str, new: str,
                      merge_budgets: bool = False) -> dict:
    """Point every reference to `old` at `new`, scoped to the household. `merge_budgets` folds a budget
    row into `new`'s existing row for the same month (deleting the orphan) to respect the (category,
    month) uniqueness; plain rename just relabels (safe because the new name is validated unused)."""
    # Settings are keyed by canonical name, so a real rename has to carry them across or the category
    # silently loses its colour, target and archive state the moment it is renamed.
    try:
        from models.category_settings import CategorySettings
        (db.query(CategorySettings)
         .filter(CategorySettings.household_id == household_id, CategorySettings.category == old)
         .update({CategorySettings.category: new}, synchronize_session=False))
    except Exception:  # noqa: BLE001 — pre-migration deploys must not break rename
        pass
    txns = (db.query(Transaction)
            .filter(Transaction.household_id == household_id, Transaction.category == old)
            .update({Transaction.category: new}, synchronize_session=False))
    rules = (db.query(CategoryRule)
             .filter(CategoryRule.household_id == household_id, CategoryRule.category == old)
             .update({CategoryRule.category: new}, synchronize_session=False))
    budget_rows = (db.query(Budget)
                   .filter(Budget.household_id == household_id, Budget.category == old).all())
    budgets = 0
    for b in budget_rows:
        if merge_budgets:
            existing = (db.query(Budget)
                        .filter(Budget.household_id == household_id, Budget.category == new,
                                Budget.month == b.month).first())
            if existing:
                existing.monthly_limit_inr = (existing.monthly_limit_inr or 0.0) + (b.monthly_limit_inr or 0.0)
                db.delete(b)
                budgets += 1
                continue
        b.category = new
        budgets += 1
    return {"transactions_moved": txns, "budgets_moved": budgets, "rules_moved": rules}
