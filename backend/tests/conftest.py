"""Test fixtures — an isolated in-memory DB + a TestClient with auth/tenancy overridden.

Was missing entirely (the redesign plan flagged it). Uses SQLite in-memory with a StaticPool so the
fixture's setup session and the request's session share the same data. Only the tables a test needs
are created (all models are SQLite-safe — no JSONB/ARRAY). The client overrides `get_db` (→ the test
session) and `get_current_household` (→ whichever household the test is acting as), so we exercise the
real endpoint + the real query scoping without cookies, sessions, or the production Postgres.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from models.audit import TransactionEvent  # noqa: F401  (registered on Base)
from models.asset import Asset  # noqa: F401  (registered on Base)
from models.budget_plan import BudgetCategory  # noqa: F401  (registered on Base)
from models.category_rule import CategoryRule  # noqa: F401  (registered on Base)
from models.category_settings import CategorySettings  # noqa: F401
from models.custom_category import CustomCategory  # noqa: F401  (registered on Base)
from models.commitment_link import CommitmentLink  # noqa: F401  (registered on Base)
from models.dismissed import DismissedDuplicate  # noqa: F401  (registered on Base)
from models.disposal import AssetDisposal, DisposalReceipt  # noqa: F401  (registered on Base)
from models.household import Budget, CashflowLine, Loan  # noqa: F401  (registered on Base)
from models.account import Account
from models.loan_payment import LoanPayment  # noqa: F401  (registered on Base)
from models.insurance import InsurancePolicy  # noqa: F401  (registered on Base)
from models.recurring import RecurringInvestment  # noqa: F401  (registered on Base)
from models.sinking import SinkingFund  # noqa: F401  (registered on Base)
from models.tenant import Household, Member  # noqa: F401  (registered on Base)
from models.transaction import EvaluatedSms, Statement, Transaction  # noqa: F401
from models.user import PasswordResetToken, User  # noqa: F401
from models.user_permission import UserPermission  # noqa: F401  (registered on Base)

_TABLES = [Household.__table__, Member.__table__, User.__table__, PasswordResetToken.__table__,
           Statement.__table__, Transaction.__table__, Loan.__table__,
           CashflowLine.__table__, Asset.__table__,
           Budget.__table__, BudgetCategory.__table__,
           SinkingFund.__table__, RecurringInvestment.__table__,
           InsurancePolicy.__table__, CategoryRule.__table__, TransactionEvent.__table__,
           CommitmentLink.__table__, DismissedDuplicate.__table__, EvaluatedSms.__table__,
           CustomCategory.__table__, CategorySettings.__table__, LoanPayment.__table__, Account.__table__,
           # Both, always: a disposal's receipts are read whenever a disposal is rendered or deleted,
           # so creating one without the other fails far from the test that forgot it.
           AssetDisposal.__table__, DisposalReceipt.__table__,
           # Both, always: User.permissions is a selectin relationship, so loading ANY user touches
           # user_permissions. Creating the one without the other fails in whatever test happens to
           # refresh a user next, nowhere near the cause.
           UserPermission.__table__,
           ]


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine, tables=_TABLES)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client_for(db_session):
    """Returns a factory: client_for(household) → TestClient acting as that household."""
    from api import (accounts_api, analytics, budget, cashflow, categories, cockpit, debt,
                     insurance, meta, networth, recurring, sinking, transactions, windfall)
    from api.deps import get_current_household

    def _make(household):
        app = FastAPI()
        app.include_router(analytics.router, prefix="/api")
        app.include_router(transactions.router, prefix="/api")
        app.include_router(debt.router, prefix="/api")
        app.include_router(cashflow.router, prefix="/api")
        app.include_router(networth.router, prefix="/api")
        app.include_router(budget.router, prefix="/api")
        app.include_router(categories.router, prefix="/api")
        app.include_router(sinking.router, prefix="/api")
        app.include_router(recurring.router, prefix="/api")
        app.include_router(insurance.router, prefix="/api")
        app.include_router(cockpit.router, prefix="/api")
        app.include_router(accounts_api.router, prefix="/api")
        app.include_router(meta.router, prefix="/api")
        app.include_router(windfall.router, prefix="/api")
        app.dependency_overrides[get_db] = lambda: db_session
        app.dependency_overrides[get_current_household] = lambda: household
        return TestClient(app)

    return _make


@pytest.fixture()
def client_for_user(db_session):
    """TestClient acting as a specific USER, not just a household.

    The profile endpoints resolve the person from the login (that is the point of them), so these
    tests need a real user identity rather than only a tenant.
    """
    from api import profile
    from api.deps import get_current_household, get_current_user

    def _make(household, user):
        app = FastAPI()
        app.include_router(profile.router, prefix="/api")
        app.dependency_overrides[get_db] = lambda: db_session
        app.dependency_overrides[get_current_household] = lambda: household
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app)

    return _make
