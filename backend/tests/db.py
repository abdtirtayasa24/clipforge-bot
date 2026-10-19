from pathlib import Path

from alembic.config import Config
from sqlalchemy.engine import make_url

BACKEND_DIR = Path(__file__).resolve().parent.parent


def libpq_url(url: str) -> str:
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


def with_database(url: str, database: str) -> str:
    return make_url(url).set(database=database).render_as_string(hide_password=False)


def alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config
