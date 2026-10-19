from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine as _create_async_engine

PSYCOPG_DRIVER = "postgresql+psycopg"
_POSTGRES_SCHEMES = {"postgres", "postgresql", PSYCOPG_DRIVER}
_LOCAL_HOSTS = {None, "", "localhost", "127.0.0.1", "::1"}


def psycopg_url(url: str) -> str:
    parsed = make_url(url.replace("postgres://", "postgresql://", 1))
    if parsed.drivername not in _POSTGRES_SCHEMES:
        raise ValueError(f"unsupported database driver: {parsed.drivername}")
    return parsed.set(drivername=PSYCOPG_DRIVER).render_as_string(hide_password=False)


def _require_tls_for_remote_hosts(url: str) -> None:
    parsed = make_url(url)
    if parsed.host not in _LOCAL_HOSTS and parsed.query.get("sslmode") != "verify-full":
        raise ValueError("remote database connections require sslmode=verify-full (ADR-004)")


def create_engine(
    url: str,
    *,
    pool_size: int = 5,
    disable_prepared_statements: bool = False,
) -> AsyncEngine:
    url = psycopg_url(url)
    _require_tls_for_remote_hosts(url)
    # Transaction-mode poolers cannot keep server-side prepared statements (ADR-004).
    connect_args = {"prepare_threshold": None} if disable_prepared_statements else {}
    return _create_async_engine(
        url,
        pool_size=pool_size,
        max_overflow=0,
        pool_pre_ping=True,
        connect_args=connect_args,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
