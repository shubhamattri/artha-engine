"""Multi-tenant core: a Household owns all financial data; Members are the people in it.

Today's free-text `holder` strings become real Member rows.
Every domain table gets a `household_id` so a new household signs up and gets a clean, isolated
ledger — the product is built for household #N, never for "us".
"""

from datetime import datetime

from database import Base
from sqlalchemy import JSON, Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String


class Household(Base):
    __tablename__ = "households"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False, default="My Household")
    currency = Column(String, nullable=True)          # falls back to settings.currency when null
    # The day of the month this household's spending cycle starts (their card's statement day).
    # Per-household, NOT global: household A may bill on the 12th and household B on the 29th.
    # Null falls back to settings.cycle_start_day. 1–31; a day past a short month's end clamps to
    # that month's last day (the 31st becomes 28 Feb), so no cycle is ever silently mis-dated.
    cycle_start_day = Column(Integer, nullable=True)
    # Per-card statement days, used ONLY when per_card_cycles is on. Also per-household: household A's
    # HDFC card may bill on the 22nd while household B's bills on the 5th. Null → settings fallback.
    per_card_cycles = Column(Boolean, nullable=True)
    card_cycle_days = Column(JSON, nullable=True)      # {"icici": 12, "hdfc": 22, ...}
    # The first cycle whose saving counts toward the wallet, as that cycle's END date. NULL means
    # the wallet has not started, and nothing accrues. Deliberately not backfilled: counting cycles
    # from before tracking began read "no records" as "saved everything" and produced a Rs 30 lakh
    # balance from months that were never captured.
    wallet_start_cycle_end = Column(String, nullable=True)
    card_cycle_default = Column(Integer, nullable=True)
    # The month this household's financial year opens in. April in India, January in much of Europe,
    # October for the US federal year — so it belongs to the household, not to the code. Null falls
    # back to April. A wrong value here does not lose data; it splits every annual figure across two
    # years, which is worse than having no annual view at all.
    year_start_month = Column(Integer, nullable=True)
    # Which income-tax regime this household files under. Decides whether 80C/80E/24(b) exist at all,
    # and therefore whether a loan's headline rate is its real cost. Null = "new", the regime a
    # household lands on without opting out — the conservative default, since it claims no relief.
    tax_regime = Column(String, nullable=True)          # "old" | "new" | null
    # When this household started actually TRACKING. Spend before it is a different regime — no
    # visibility, no targets — so it must not inform a suggested target. Without this the engine
    # reads pre-tracking months as evidence and advises raising budgets back to the behaviour the
    # household set out to change, which is advice to give up. Null = no baseline, use everything.
    budget_baseline_from = Column(Date, nullable=True)
    # Member keys whose house property is LET OUT. The ₹2L 24(b) cap is self-occupied only; let-out
    # interest stays fully deductible, which changes the effective rate materially.
    let_out_members = Column(JSON, nullable=True)       # member keys, e.g. ["Member A", ...]
    # A return this household MEASURED somewhere Artha cannot see — a broker's realised XIRR, read off
    # their console and typed in. It is not derived here and it is not an assumption, so api/windfall
    # labels it its own third thing. NULL = nothing entered, and nothing is substituted.
    # The other three columns are the reason this is safe to use: a hand-entered rate goes stale
    # silently, so it may not be stored without the date it measures TO and the moment a human typed
    # it. `scope` is what it refers to, in the household's own words.
    measured_return_pct = Column(Float, nullable=True)
    measured_return_scope = Column(String, nullable=True)
    measured_return_as_of = Column(Date, nullable=True)
    measured_return_entered_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Member(Base):
    """A person in the household. Replaces the free-text `holder` column everywhere.
    `key` is the stable label used by legacy rows (the holder string, or "Household")."""

    __tablename__ = "members"

    id = Column(Integer, primary_key=True, autoincrement=True)
    household_id = Column(Integer, ForeignKey("households.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    key = Column(String, nullable=False)              # matches legacy holder strings
    is_household = Column(Boolean, nullable=False, default=False)  # the shared "Household" pseudo-member
    active = Column(Boolean, nullable=False, default=True)

    # Identity a bank needs before it will hand over a statement. Banks lock statement PDFs with a
    # password derived from exactly these (see parsers/pdf_password_rules), so without them the
    # monthly reconciliation cannot open the file at all. Collected at onboarding rather than asked
    # for later, because a household that skips this silently loses statement import.
    # PER MEMBER, not per household: a card in the partner's name is locked with THEIR details.
    dob = Column(Date, nullable=True)
    mobile = Column(String, nullable=True)
    pan = Column(String, nullable=True)
    pincode = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
