"""Per-category settings that apply to EVERY category, built-in or household-defined."""

from __future__ import annotations

from database import Base
from sqlalchemy import Boolean, Column, Float, ForeignKey, Integer, String, UniqueConstraint


class CategorySettings(Base):
    """A household's overrides for one category, keyed by the canonical category name.

    Category management was built against `custom_categories`, which meant it only worked for
    categories a household had invented. The built-in taxonomy is an enum, so a household using only
    built-ins (the normal case) had a management screen that listed nothing. This table exists so the
    colour, target, archive and committed flags work the same for Groceries as for one you added.

    `category` is the CANONICAL name, exactly as stored on Transaction.category, and that is
    deliberate: it is the join key for everything, so it must not move.

    `display_name` is the one property that cannot work the same way for both. Forty places in this
    codebase compare against `Category.*.value`, including the AI classifier's prompt, which embeds
    the names as literal text — so renaming a built-in by rewriting the stored name would drop it out
    of the Plan pool and change what the model is told. A built-in therefore gets a label over a stable
    key. A custom category has no such constraint and keeps its real rewrite (api/categories rename),
    which is why that path also updates this row's `category` to match.

    `is_variable` is nullable ON PURPOSE. NULL means "whatever the taxonomy says", so a built-in keeps
    its own answer forever unless the household deliberately overrides it. A non-null value is a
    decision someone made, and it can be told apart from one nobody has touched.
    """

    __tablename__ = "category_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    household_id = Column(Integer, ForeignKey("households.id"), nullable=False,
                          index=True, server_default="1")
    category = Column(String, nullable=False)
    display_name = Column(String, nullable=True)
    color = Column(String, nullable=True)
    active = Column(Boolean, nullable=False, default=True, server_default="1")
    is_variable = Column(Boolean, nullable=True)
    sort_order = Column(Integer, nullable=True)
    default_target_inr = Column(Float, nullable=True)

    __table_args__ = (
        UniqueConstraint("household_id", "category", name="uq_category_settings_household_category"),
    )
