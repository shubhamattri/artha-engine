from datetime import datetime

from database import Base
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String


class Account(Base):
    """A bank account or card the household owns.

    This replaces the hardcoded OWN_ACCOUNTS dict in services/account_registry.py. That dict meant
    onboarding a new card required a developer editing a tuple and redeploying — fine for one
    household, impossible for a product. Two jobs depend on this table:

      1. RECONCILIATION — knowing a last4 is ours is how the engine tells "money moved between our own
         accounts" apart from real external income/expense (see ALGORITHM 3 in account_registry).
      2. SMS CAPTURE — `sms_sender` holds the SMS header the bank sends from (e.g. BOBCRD). Anything
         not matching a registered sender is dropped, which is how a new card silently goes missing.
    """

    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    household_id = Column(Integer, ForeignKey("households.id"), nullable=False,
                          index=True, server_default="1")
    bank = Column(String, nullable=False)                  # HDFC, ICICI, Bank of Baroda, ...
    kind = Column(String, nullable=False, default="card")  # card | account
    last4 = Column(String(4), nullable=False)
    holder = Column(String, nullable=True)                 # a member key, or "Household"
    label = Column(String, nullable=True)                  # "RuPay credit card", "salary", ...
    # Comma-separated SMS sender fragments, e.g. "BOBCRD,BOBTXN". Matched case-insensitively as a
    # substring of the sender header (VD-BOBCRD-S contains BOBCRD).
    sms_senders = Column(String, nullable=True)
    # ---- monthly statement reconciliation (distinct from the daily transaction sync) ----
    # The DAILY sync captures transactions as they happen. This is the MONTHLY audit: the bank's own
    # statement, which is the authority you reconcile the daily capture against. It is keyed to the
    # card's own statement date, not a nightly cron, because the statement only exists once a month.
    #
    # statement_sender is the stable key. Banks mask the card number in filenames and subjects
    # ("4854XXXXXXXXXX42"), so matching on last4 fails, but the from-address never changes.
    #
    # Sender alone is not always enough: one bank mails two cards' statements on different days, and the
    # card on the 3rd from ONE address, so the two schedules merge into a bimodal series that reads
    # as unstable. statement_subject_hint is the discriminator when that happens, NULL when the
    # sender already identifies the card.
    statement_sender = Column(String, nullable=True)
    statement_subject_hint = Column(String, nullable=True)
    statement_day = Column(Integer, nullable=True)      # day of month the email actually arrives
    statement_run_day = Column(Integer, nullable=True)  # when to fetch; defaults to statement_day + 1
    statement_last_imported = Column(String, nullable=True)   # yyyy-mm of the newest one ingested

    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
