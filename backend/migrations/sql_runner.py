from pathlib import Path

from alembic import op

SQL_DIR = Path(__file__).resolve().parent / "sql"


def run_sql(filename: str) -> None:
    sql = (SQL_DIR / filename).read_text(encoding="utf-8")
    # Use the raw psycopg cursor: SQLAlchemy text() would treat ":word" inside the SQL
    # (JSON literals, casts) as bind parameters, and psycopg only accepts multiple
    # statements when no parameters are passed.
    dbapi_connection = op.get_bind().connection.dbapi_connection
    if dbapi_connection is None:
        raise RuntimeError("migration connection is closed")
    with dbapi_connection.cursor() as cursor:
        cursor.execute(sql)
