from collections.abc import AsyncIterator

import pytest
from sqlalchemy import update

from clipforge.db.models import ProcessingJob
from clipforge.db.session import create_engine, create_session_factory
from clipforge.queue.repository import ACTIVE_STATUSES, JobQueue
from tests.clock import FakeClock
from tests.jobs import Sessions


@pytest.fixture
async def sessions(migrated_database_url: str) -> AsyncIterator[Sessions]:
    """Sessions on the shared test database with no claimable job left by earlier tests."""
    engine = create_engine(migrated_database_url, pool_size=3)
    factory = create_session_factory(engine)
    async with factory.begin() as session:
        await session.execute(
            update(ProcessingJob)
            .where(ProcessingJob.status.in_(ACTIVE_STATUSES))
            .values(status="cancelled", lease_token=None, lease_expires_at=None)
        )
    yield factory
    await engine.dispose()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def queue(sessions: Sessions, clock: FakeClock) -> JobQueue:
    return JobQueue(sessions, clock=clock, jitter=lambda: 0.0)
