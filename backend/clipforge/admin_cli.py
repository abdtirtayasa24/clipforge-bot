"""Bootstrap and manage administrators: python -m clipforge.admin_cli add-admin --telegram-id N"""

import argparse
import asyncio
import logging
import sys

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert

from clipforge.config import Settings
from clipforge.db.models import AdminUser
from clipforge.db.session import create_engine, create_session_factory
from clipforge.logging import configure_logging

logger = logging.getLogger(__name__)

EXIT_OK, EXIT_NOT_FOUND, EXIT_CONFIG = 0, 1, 2


def _telegram_id(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be an integer") from None
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m clipforge.admin_cli")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add-admin", help="create or re-activate an administrator")
    add.add_argument("--telegram-id", type=_telegram_id, required=True)
    add.add_argument("--name")
    deactivate = commands.add_parser("deactivate-admin", help="revoke an administrator")
    deactivate.add_argument("--telegram-id", type=_telegram_id, required=True)
    return parser


async def _run(args: argparse.Namespace, database_url: str) -> int:
    engine = create_engine(database_url, pool_size=1)
    try:
        async with create_session_factory(engine).begin() as session:
            if args.command == "add-admin":
                values: dict[str, object] = {"telegram_user_id": args.telegram_id, "active": True}
                if args.name is not None:
                    values["display_name"] = args.name
                await session.execute(
                    insert(AdminUser)
                    .values(values)
                    .on_conflict_do_update(index_elements=["telegram_user_id"], set_=values)
                )
                logger.info("admin enabled", extra={"telegram_user_id": args.telegram_id})
                return EXIT_OK
            result = await session.execute(
                update(AdminUser)
                .where(AdminUser.telegram_user_id == args.telegram_id)
                .values(active=False)
            )
            if result.rowcount == 0:  # type: ignore[attr-defined]
                logger.error("admin not found", extra={"telegram_user_id": args.telegram_id})
                return EXIT_NOT_FOUND
            logger.info("admin deactivated", extra={"telegram_user_id": args.telegram_id})
            return EXIT_OK
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = Settings()
    configure_logging(settings.log_level)
    if settings.database_url is None:
        logger.error("CLIPFORGE_DATABASE_URL is not set")
        return EXIT_CONFIG
    # psycopg's async mode cannot run on Windows' default Proactor loop.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    return asyncio.run(
        _run(args, settings.database_url.get_secret_value()), loop_factory=loop_factory
    )


if __name__ == "__main__":
    sys.exit(main())
