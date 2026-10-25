"""Canonical taxonomy — ONE source of truth for categories + transaction types.

Two tiers:
  • VARIABLE spend (10) — the everyday buckets a household actually budgets. These are the ONLY
    categories shown in the spend picker and tracked in the Plan.
  • Money-movement — income, card-bill payments, transfers, EMIs, savings, insurance, rent,
    donations, fees, refunds. Auto-classified, hidden from the everyday picker. Some of these are
    still real outflows (RENT, INSURANCE, DONATION); they are excluded from the variable Plan
    because they are committed, not because the money did not leave.

Tightened from 21 → 18 (10 spend) on 2026-06-24 per the owner: merged Home→Shopping,
UPI+Others+ATM→Personal/Misc, Capital Inflow→Income, and renamed for clarity.
"""

from __future__ import annotations

from enum import Enum


class TxnType(str, Enum):
    SPEND = "SPEND"
    INCOME = "INCOME"
    EMI = "EMI"
    SAVING = "SAVING"
    TRANSFER = "TRANSFER"
    PAYMENT = "PAYMENT"   # credit-card bill payment
    REFUND = "REFUND"
    FEE = "FEE"


class Category(str, Enum):
    # — the 10 everyday spend buckets (shown in the picker, budgeted in the Plan) —
    FOOD_DINING = "Food & Dining"
    GROCERIES = "Groceries"
    TRANSPORT = "Transport"
    SHOPPING = "Shopping"
    BILLS = "Bills & Utilities"
    HEALTH = "Health"
    ENTERTAINMENT = "Entertainment & Subscriptions"
    TRAVEL = "Travel"
    EDUCATION = "Education"
    PERSONAL = "Personal / Misc"
    # — money movement (auto, not everyday spend, hidden from the picker) —
    INCOME = "Income"
    CC_PAYMENT = "Credit Card Payment"
    INTERNAL_TRANSFER = "Internal Transfer"
    LOAN_EMI = "Loan EMI"
    SAVINGS = "Savings & Investments"
    RENT = "Rent / Housing"                 # fixed committed housing cost — real outflow, not variable
    DONATION = "Donation"                  # charitable giving — leaves permanently, never becomes wealth
    INSURANCE = "Insurance"
    FEES = "Fees & Charges"
    REFUND = "Refund"
    SETTLEMENT = "Settlement / Repayment"  # money returned/repaid — not spend
    LENDING = "Lending"                    # money lent out (you expect it back) — not spend
    REIMBURSABLE = "Reimbursable"          # office/work expense you get reimbursed for — not personal spend


# The everyday spend set — order is the display order in pickers and the Plan.
VARIABLE_CATEGORIES: list[Category] = [
    Category.FOOD_DINING, Category.GROCERIES, Category.TRANSPORT, Category.SHOPPING,
    Category.BILLS, Category.HEALTH, Category.ENTERTAINMENT, Category.TRAVEL,
    Category.EDUCATION, Category.PERSONAL,
]
_VARIABLE_VALUES = {c.value for c in VARIABLE_CATEGORIES}
_VALUES = {c.value for c in Category}   # every built-in name — a custom category may not collide with these

# category → default txn_type for a DEBIT (credits are handled direction-aware in the classifier).
_TYPE: dict[Category, TxnType] = {
    **{c: TxnType.SPEND for c in VARIABLE_CATEGORIES},
    Category.INCOME: TxnType.INCOME,
    Category.CC_PAYMENT: TxnType.PAYMENT,
    Category.INTERNAL_TRANSFER: TxnType.TRANSFER,
    Category.LOAN_EMI: TxnType.EMI,
    Category.SAVINGS: TxnType.SAVING,
    Category.RENT: TxnType.SPEND,        # a real outflow, but committed & fixed → excluded from variable Plan
    # SPEND, not SAVING: an RD funding a donation looks like saving on the way in, but the money is
    # committed to leave. Counting it as savings inflates net worth with money that never becomes it.
    Category.DONATION: TxnType.SPEND,
    Category.INSURANCE: TxnType.SPEND,   # a real outflow, but committed → excluded from the variable Plan
    Category.FEES: TxnType.FEE,
    Category.REFUND: TxnType.REFUND,
    Category.SETTLEMENT: TxnType.TRANSFER,  # excluded from spend, like a transfer
    Category.LENDING: TxnType.TRANSFER,     # excluded from spend (money out you expect back)
    Category.REIMBURSABLE: TxnType.TRANSFER,  # office expense you get reimbursed → not personal spend
}
CATEGORY_TYPE: dict[str, str] = {c.value: t.value for c, t in _TYPE.items()}

# Old category names (pre-2026-06-24) → new. Used to migrate existing rows + remap any stragglers.
MERGE: dict[str, str] = {
    "Utilities & Bills": "Bills & Utilities",
    "Health & Medical": "Health",
    "Subscriptions & Entertainment": "Entertainment & Subscriptions",
    "Home & Furniture": "Shopping",
    "Salary / Income": "Income",
    "Capital Inflow": "Income",
    "Personal & UPI": "Personal / Misc",
    "Others": "Personal / Misc",
    "ATM & Cash": "Personal / Misc",
}


def is_variable(category: str | None, db=None, household_id: int | None = None) -> bool:
    """True for an everyday spend category — what the Plan budgets and the picker shows. Covers the 10
    built-ins always, plus this household's user-defined budget categories when (db, household_id) are
    given. Without them it answers for built-ins only (the safe default for callers with no session)."""
    # A household override wins over the taxonomy, for a BUILT-IN as much as for a custom category.
    # Travel is everyday spend for one household and a committed annual cost for another, and this
    # check used to short-circuit on enum membership before anything could say so.
    override = category_settings(db, household_id).get(category or "", {}).get("is_variable")
    if override is not None:
        return bool(override)
    if category in _VARIABLE_VALUES:
        return True
    # No override and not a built-in variable: it is everyday spend if the household added it, which
    # is what adding a budget category means. Committing it is done through category_settings above.
    return category in set(custom_category_names(db, household_id))


def custom_category_names(db=None, household_id: int | None = None) -> list[str]:
    """This household's user-defined budget categories, in display order. Empty when there's no session
    or household to scope to (single-household-safe). Never raises — a missing table (pre-migration)
    degrades to no custom categories rather than breaking the taxonomy.

    Archived and committed are NOT filtered here. Both live in category_settings, keyed by canonical
    name so they apply to a built-in as much as to one of these, and the callers that care read them
    from there. This function answers one question: which names did the household add.
    """
    if db is None or household_id is None:
        return []
    try:
        from models.custom_category import CustomCategory
        q = (db.query(CustomCategory)
             .filter(CustomCategory.household_id == household_id)
             .order_by(CustomCategory.sort_order, CustomCategory.id))
        # a custom name can never shadow a built-in (enforced on create), but guard anyway
        return [r.name for r in q.all() if r.name not in _VALUES]
    except Exception:
        return []


def variable_category_values(db=None, household_id: int | None = None) -> list[str]:
    """The full everyday-spend set the Plan budgets and the pickers show: the 10 built-ins (in order)
    followed by this household's custom budget categories. ONE place every 'what counts as a budget
    category' answer comes from, so adding a category surfaces everywhere at once."""
    settings = category_settings(db, household_id)

    def _wanted(name: str, default: bool) -> bool:
        ov = settings.get(name, {}).get("is_variable")
        return default if ov is None else bool(ov)

    # Built-ins the household has NOT marked committed, plus any category (built-in or custom) it has
    # deliberately marked everyday. One list, one rule, so the Plan pool and the picker cannot drift.
    builtins = [c.value for c in VARIABLE_CATEGORIES if _wanted(c.value, True)]
    promoted = [c.value for c in Category
                if c not in VARIABLE_CATEGORIES and _wanted(c.value, False)]
    customs = [n for n in custom_category_names(db, household_id) if _wanted(n, True)]
    return builtins + promoted + customs


def canonical_name(category: str | None) -> str:
    """Map any (possibly legacy) category string onto the current taxonomy."""
    if not category:
        return Category.PERSONAL.value
    return MERGE.get(category, category)


def category_settings(db=None, household_id: int | None = None) -> dict[str, dict]:
    """Every category setting this household has, keyed by canonical category name.

    Degrades to {} if the table is not there yet, so a deploy that lands before its migration shows
    the plain taxonomy rather than 500ing every screen that renders a category.
    """
    if db is None or household_id is None:
        return {}
    try:
        from models.category_settings import CategorySettings
        rows = (db.query(CategorySettings)
                .filter(CategorySettings.household_id == household_id).all())
        return {r.category: {"display_name": r.display_name, "color": r.color,
                             "active": bool(r.active), "is_variable": r.is_variable,
                             "sort_order": r.sort_order,
                             "default_target_inr": r.default_target_inr} for r in rows}
    except Exception:
        return {}


def _custom_ids(db=None, household_id: int | None = None) -> dict[str, int]:
    """Name to row id for this household's own categories. Empty pre-migration or without a session."""
    if db is None or household_id is None:
        return {}
    try:
        from models.custom_category import CustomCategory
        rows = (db.query(CustomCategory)
                .filter(CustomCategory.household_id == household_id)
                .order_by(CustomCategory.sort_order, CustomCategory.id).all())
        return {r.name: r.id for r in rows if r.name not in _VALUES}
    except Exception:
        return {}


def _current_month_targets(db=None, household_id: int | None = None) -> dict[str, float]:
    """This month's actual budget target per category — the number shown on the Plan.

    A category's target lives in `budgets`, one row per (category, month). The per-category
    `default_target_inr` is only a seed for months that have no row. Reading the default alone made
    every row on the management screen say "no target" while the Plan beside it showed Groceries at
    40,000, because nobody had ever set a default: the screen was answering a question nobody asked.
    """
    if db is None or household_id is None:
        return {}
    try:
        from datetime import date

        from models.household import Budget
        month = date.today().strftime("%Y-%m")
        rows = (db.query(Budget)
                .filter(Budget.household_id == household_id, Budget.month == month).all())
        return {r.category: float(r.monthly_limit_inr or 0.0) for r in rows}
    except Exception:
        return {}


def all_manageable_categories(db=None, household_id: int | None = None) -> list[dict]:
    """ONE list of every category a household can manage: the built-in taxonomy and its own additions.

    The management screen used to show only custom categories, because those are a table and the
    built-ins are an enum. That is an implementation detail, and a household whose categories are all
    built-in opened the screen to an empty list while Groceries and Travel sat on the Plan behind it.

    `builtin` says which kind each one is, because exactly one thing differs: renaming a built-in sets
    a display label over a stable key, while renaming a custom category rewrites the household's data.
    Everything else — colour, target, archive, committed — behaves identically.
    """
    settings = category_settings(db, household_id)
    custom_ids = _custom_ids(db, household_id)
    this_month = _current_month_targets(db, household_id)
    custom = set(custom_ids)
    out: list[dict] = []
    for name in [c.value for c in Category] + list(custom):
        if any(o["category"] == name for o in out):
            continue
        st = settings.get(name, {})
        out.append({
            "category": name,
            "display_name": st.get("display_name") or name,
            "builtin": name not in custom,
            # Only a household's own category has a row id, and only those can be renamed for real,
            # merged, split or deleted. A built-in has no row and cannot be removed from the taxonomy.
            "id": custom_ids.get(name),
            "color": st.get("color"),
            "active": st.get("active", True),
            # None = the taxonomy decides. Resolved here so callers never re-derive it.
            "is_variable": (st["is_variable"] if st.get("is_variable") is not None
                            else is_variable(name, db, household_id)),
            "sort_order": st.get("sort_order"),
            "default_target_inr": st.get("default_target_inr"),
            # What this category is actually budgeted at RIGHT NOW, from the month's own budget row.
            # None means the month has no row, which is different from a row set to zero.
            "current_target_inr": this_month.get(name),
        })
    out.sort(key=lambda o: (o["sort_order"] if o["sort_order"] is not None else 10_000,
                            o["display_name"].lower()))
    return out
