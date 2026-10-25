"""Import EVERY model here so a plain `import models` registers all tables on the SQLAlchemy
metadata. Standalone entry points (scripts/daily_sync.py, one-off scripts) rely on this — without
the full set, cross-table foreign keys (e.g. transactions.household_id → households) fail to resolve
at flush time with "could not find table 'households'", crashing the whole job.
"""

from models.account import Account
from models.advisor_chat import AdvisorChat, AdvisorMessage
from models.asset import Asset
from models.audit import TransactionEvent
from models.budget_plan import BudgetCategory
from models.category_rule import CategoryRule
from models.commitment_link import CommitmentLink
from models.custom_category import CustomCategory
from models.dismissed import DismissedDuplicate
from models.disposal import AssetDisposal
from models.household import Budget, CashflowLine, Loan
from models.insurance import InsurancePolicy
from models.loan_payment import LoanPayment
from models.recurring import RecurringInvestment
from models.sinking import SinkingFund
from models.tenant import Household, Member
from models.transaction import DeletedFingerprint, Statement, Transaction
from models.user import PasswordResetToken, User

__all__ = [
    "Asset", "TransactionEvent", "BudgetCategory", "CategoryRule", "Budget", "CashflowLine", "Loan",
    "InsurancePolicy", "RecurringInvestment", "SinkingFund", "Household", "Member",
    "DeletedFingerprint", "Statement", "Transaction", "PasswordResetToken", "User",
    "AdvisorChat", "AdvisorMessage", "CommitmentLink", "DismissedDuplicate",
    "Account", "CustomCategory", "CategorySettings", "AssetDisposal", "LoanPayment",
]
from models.sync_run import SyncRun  # noqa: F401,E402
from models.surplus import SurplusAllocation, SurplusDraw  # noqa: F401,E402
from models.category_settings import CategorySettings  # noqa: F401,E402
from models.user_permission import UserPermission  # noqa: F401,E402
