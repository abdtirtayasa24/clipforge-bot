import pytest

from clipforge.db.session import create_engine, psycopg_url


@pytest.mark.parametrize(
    "raw",
    [
        "postgres://u:p@localhost:5432/db",
        "postgresql://u:p@localhost:5432/db",
        "postgresql+psycopg://u:p@localhost:5432/db",
    ],
)
def test_psycopg_url_uses_psycopg_driver(raw: str) -> None:
    assert psycopg_url(raw) == "postgresql+psycopg://u:p@localhost:5432/db"


def test_psycopg_url_rejects_other_schemes() -> None:
    with pytest.raises(ValueError):
        psycopg_url("mysql://u:p@localhost/db")


async def test_engine_pool_is_sized_for_one_process() -> None:
    engine = create_engine("postgresql://u:p@localhost/db", pool_size=3)

    assert engine.pool.size() == 3  # type: ignore[attr-defined]
    assert engine.url.drivername == "postgresql+psycopg"
    await engine.dispose()


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://u:p@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres",
        "postgresql://u:p@db.example.com/postgres?sslmode=require",
    ],
)
def test_remote_hosts_require_verify_full_tls(url: str) -> None:
    with pytest.raises(ValueError, match="verify-full"):
        create_engine(url)


async def test_remote_host_with_verify_full_is_accepted() -> None:
    engine = create_engine(
        "postgresql://u:p@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres"
        "?sslmode=verify-full&sslrootcert=/etc/clipforge-bot/supabase-ca.crt"
    )

    assert engine.url.query["sslmode"] == "verify-full"
    await engine.dispose()
