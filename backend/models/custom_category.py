from datetime import datetime

from database import Base
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)


class CustomCategory(Base):
    """A household-defined budget category. These extend the built-in taxonomy (services.categories)
    with the household's own everyday-spend buckets — added from the Plan UI. Every custom category is a
    VARIABLE spend bucket (that is what "budget category" means: you set a monthly target and track spend
    against it), so it inherits txn_type=SPEND via the CATEGORY_TYPE default. Scoped per household; the
    name is unique within a household and may not collide with a built-in category name (enforced in the
    API layer). No enum change is needed — Transaction.category is a free string and CATEGORY_TYPE.get
    already defaults unknown names to SPEND."""

    __tablename__ = "custom_categories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    household_id = Column(Integer, ForeignKey("households.id"), nullable=False,
                          index=True, server_default="1")
    name = Column(String, nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)
    # How this category BEHAVES and LOOKS lives in category_settings, keyed by canonical name, so a
    # built-in and a household's own addition are managed the same way. This table is only the
    # identity of a category the built-in enum cannot express.
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("household_id", "name", name="uq_custom_category_household_name"),
    )
