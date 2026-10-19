from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, inspect
from sqlalchemy.dialects import postgresql

from clipforge.db.models import Base
from clipforge.db.session import psycopg_url

DIALECT = postgresql.dialect()  # type: ignore[no-untyped-call]


@pytest.fixture(scope="module")
def sync_engine(migrated_database_url: str) -> Iterator[Engine]:
    engine = create_engine(psycopg_url(migrated_database_url))
    yield engine
    engine.dispose()


def describe_db(engine: Engine) -> dict[str, dict[str, tuple[str, bool]]]:
    inspector = inspect(engine)
    return {
        table: {
            col["name"]: (col["type"].compile(dialect=DIALECT), col["nullable"])
            for col in inspector.get_columns(table)
        }
        for table in inspector.get_table_names()
        if table != "alembic_version"
    }


def describe_orm() -> dict[str, dict[str, tuple[str, bool]]]:
    return {
        table.name: {
            col.name: (col.type.compile(dialect=DIALECT), bool(col.nullable))
            for col in table.columns
        }
        for table in Base.metadata.sorted_tables
    }


def test_orm_tables_match_migrated_schema(sync_engine: Engine) -> None:
    assert sorted(describe_orm()) == sorted(describe_db(sync_engine))


def test_orm_columns_types_and_nullability_match(sync_engine: Engine) -> None:
    db = describe_db(sync_engine)

    for table, columns in describe_orm().items():
        assert columns == db[table], table


def test_orm_primary_keys_match(sync_engine: Engine) -> None:
    inspector = inspect(sync_engine)

    for table in Base.metadata.sorted_tables:
        expected = [col.name for col in table.primary_key.columns]
        actual = inspector.get_pk_constraint(table.name)["constrained_columns"]
        assert sorted(expected) == sorted(actual), table.name
