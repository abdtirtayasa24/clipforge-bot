import os

from alembic import context
from sqlalchemy import create_engine, pool

from clipforge.db.session import psycopg_url

MIGRATION_DATABASE_URL_ENV = "CLIPFORGE_MIGRATION_DATABASE_URL"


def database_url() -> str:
    url = context.config.get_main_option("sqlalchemy.url") or os.environ.get(
        MIGRATION_DATABASE_URL_ENV
    )
    if not url:
        raise RuntimeError(f"{MIGRATION_DATABASE_URL_ENV} is not set")
    return psycopg_url(url)


def run_migrations_online() -> None:
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("offline migrations are not supported; the SQL files are the script")
run_migrations_online()
