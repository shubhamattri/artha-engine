import os
import sys
from logging.config import fileConfig

from alembic import context

# Add backend dir (parent of alembic/) to sys.path so flat imports work.
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from config import settings  # noqa: E402
from database import Base, engine  # noqa: E402
from models.transaction import Statement, Transaction  # noqa: E402,F401
from models.household import Loan, Budget  # noqa: E402,F401
from models.recurring import RecurringInvestment  # noqa: E402,F401
from models.sinking import SinkingFund  # noqa: E402,F401
from models.budget_plan import BudgetCategory  # noqa: E402,F401
from models.insurance import InsurancePolicy  # noqa: E402,F401

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Inject the runtime DB URL from app config (env / .env) so credentials are
# never hardcoded in alembic.ini.
config.set_main_option("sqlalchemy.url", settings.db_url)

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
