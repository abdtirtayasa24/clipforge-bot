"""Run YouTube discovery once: python -m clipforge.discovery_cli (run by the hourly timer)."""

import argparse
import asyncio
import logging
import sys
from datetime import datetime

import httpx

from clipforge.config import Settings
from clipforge.db.session import create_engine, create_session_factory
from clipforge.discovery.service import (
    DiscoveryAlreadyRunning,
    DiscoverySettings,
    run_exclusive,
)
from clipforge.logging import configure_logging
from clipforge.notify.telegram import telegram_client
from clipforge.pipeline.stages.notify import Notifier
from clipforge.settings_schema import load_settings

logger = logging.getLogger(__name__)

EXIT_OK, EXIT_FAILED, EXIT_CONFIG = 0, 1, 2


def scheduled_run_due(discovery: DiscoverySettings, now: datetime) -> bool:
    return discovery.auto_enabled and now.hour == discovery.auto_hour


async def _run(
    settings: Settings,
    database_url: str,
    api_key: str,
    *,
    scheduled_at: datetime | None = None,
) -> int:
    """`scheduled_at` is set for timer runs, which go ahead only in the configured hour."""
    engine = create_engine(
        database_url,
        pool_size=2,
        disable_prepared_statements=settings.db_disable_prepared_statements,
    )
    try:
        if scheduled_at is not None:
            async with create_session_factory(engine)() as session:
                discovery = (await load_settings(session)).discovery
            if not scheduled_run_due(discovery, scheduled_at):
                logger.info("scheduled discovery is not due; skipped")
                return EXIT_OK
        report = await run_exclusive(engine, api_key)
        async with httpx.AsyncClient() as http:
            notifier = Notifier(create_session_factory(engine), telegram_client(settings, http))
            await notifier.discovery_summary(report)
    except DiscoveryAlreadyRunning:
        # Another run (manual or timer) covers this one; skipping is not a failure.
        logger.warning("discovery is already running; skipped")
        return EXIT_OK
    except Exception:
        logger.exception("discovery failed")
        return EXIT_FAILED
    finally:
        await engine.dispose()
    return EXIT_OK if report.ok else EXIT_FAILED


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m clipforge.discovery_cli")
    parser.add_argument(
        "--scheduled",
        action="store_true",
        help="run only if auto-discovery is enabled for the current hour (used by the timer)",
    )
    args = parser.parse_args(argv)
    settings = Settings()
    configure_logging(settings.log_level)
    if settings.database_url is None:
        logger.error("CLIPFORGE_DATABASE_URL is not set")
        return EXIT_CONFIG
    if settings.youtube_api_key is None:
        logger.error("YOUTUBE_API_KEY is not set")
        return EXIT_CONFIG
    # psycopg's async mode cannot run on Windows' default Proactor loop.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    return asyncio.run(
        _run(
            settings,
            settings.database_url.get_secret_value(),
            settings.youtube_api_key.get_secret_value(),
            scheduled_at=datetime.now() if args.scheduled else None,
        ),
        loop_factory=loop_factory,
    )


if __name__ == "__main__":
    sys.exit(main())
