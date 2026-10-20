import json
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import psycopg
import pytest
import respx
from sqlalchemy import delete, insert

from clipforge.db.models import AppSetting, Clip, ClipCandidate
from clipforge.discovery.service import DiscoveryReport
from clipforge.notify.telegram import TelegramClient
from clipforge.pipeline.context import StageContext
from clipforge.pipeline.runner import PipelineRunner
from clipforge.pipeline.stage import StageResult
from clipforge.pipeline.stages.notify import NOTIFICATIONS_KEY, Notifier
from clipforge.queue.errors import PermanentError
from clipforge.queue.repository import JobQueue
from clipforge.storage.drive_oauth import DriveAuthExpired
from tests.db import libpq_url
from tests.jobs import Sessions, claim, load_job, make_queued_job

BOT_TOKEN = "123456:TEST-token_value"
SEND_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"


class ClipStage:
    """Creates one clip for the job, as render + upload would."""

    name = "uploading"
    running_name: str | None = None

    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def run(self, ctx: StageContext) -> StageResult:
        async with self._sessions.begin() as session:
            candidate_id = await session.scalar(
                insert(ClipCandidate)
                .values(
                    job_id=ctx.job.lease.job_id,
                    start_ms=0,
                    end_ms=30_000,
                    hook_score=88,
                    emotion_score=88,
                    clarity_score=88,
                    relevance_score=88,
                    visual_score=88,
                    completeness_score=88,
                    total_score=88,
                    title="Judul asli",
                    selected=True,
                    rank=1,
                )
                .returning(ClipCandidate.id)
            )
            await session.execute(
                insert(Clip).values(
                    candidate_id=candidate_id,
                    job_id=ctx.job.lease.job_id,
                    video_id=ctx.job.video_id,
                    start_ms=0,
                    end_ms=30_000,
                    title="Judul asli",
                    title_override="Judul pilihan",
                )
            )
        return StageResult()


class FailingStage:
    name = "uploading"
    running_name: str | None = None

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def run(self, ctx: StageContext) -> StageResult:
        raise self._error


@pytest.fixture
def admin_chat(migrated_database_url: str) -> Iterator[int]:
    chat_id = int(uuid.uuid4().int % 10**9) + 1
    with psycopg.connect(libpq_url(migrated_database_url), autocommit=True) as conn:
        conn.execute("INSERT INTO admin_users (telegram_user_id) VALUES (%s)", (chat_id,))
        yield chat_id
        conn.execute("DELETE FROM admin_users WHERE telegram_user_id = %s", (chat_id,))


@pytest.fixture
def telegram() -> Iterator[respx.Route]:
    with respx.mock(assert_all_called=False) as router:
        yield router.post(SEND_URL).respond(json={"ok": True, "result": {}})


@pytest.fixture
async def notifier(sessions: Sessions) -> AsyncIterator[Notifier]:
    async with httpx.AsyncClient() as http:
        yield Notifier(sessions, TelegramClient(http, BOT_TOKEN))
    async with sessions.begin() as session:
        await session.execute(delete(AppSetting).where(AppSetting.key == NOTIFICATIONS_KEY))


def messages_to(route: respx.Route, chat_id: int) -> list[str]:
    bodies = [json.loads(call.request.content) for call in route.calls]
    return [body["text"] for body in bodies if body["chat_id"] == chat_id]


async def run_job(
    queue: JobQueue, sessions: Sessions, stage: object, notifier: Notifier, tmp_path: Path
) -> tuple[uuid.UUID, str]:
    job_id = await make_queued_job(queue, sessions)
    runner = PipelineRunner(queue, [stage], workspace_root=tmp_path, notifier=notifier)  # type: ignore[list-item]
    return job_id, await runner.run(await claim(queue))


async def test_completed_job_notifies_clips_ready_with_effective_titles(
    queue: JobQueue,
    sessions: Sessions,
    notifier: Notifier,
    telegram: respx.Route,
    admin_chat: int,
    tmp_path: Path,
) -> None:
    _, status = await run_job(queue, sessions, ClipStage(sessions), notifier, tmp_path)

    assert status == "completed"
    [text] = messages_to(telegram, admin_chat)
    assert "1 clips ready for review" in text
    assert "1. Judul pilihan (score 88)" in text
    assert "Estimated AI cost: 0.0000 USD" in text
    assert BOT_TOKEN not in text


async def test_permanently_failed_job_notifies_the_failure(
    queue: JobQueue,
    sessions: Sessions,
    notifier: Notifier,
    telegram: respx.Route,
    admin_chat: int,
    tmp_path: Path,
) -> None:
    stage = FailingStage(PermanentError("source has no audio"))

    _, status = await run_job(queue, sessions, stage, notifier, tmp_path)

    assert status == "failed"
    [text] = messages_to(telegram, admin_chat)
    assert text.startswith("ClipForge: job failed")
    assert "source has no audio" in text


async def test_drive_disconnection_is_reported_while_the_job_retries(
    queue: JobQueue,
    sessions: Sessions,
    notifier: Notifier,
    telegram: respx.Route,
    admin_chat: int,
    tmp_path: Path,
) -> None:
    stage = FailingStage(DriveAuthExpired("reconnect Drive"))

    _, status = await run_job(queue, sessions, stage, notifier, tmp_path)

    assert status == "retry_scheduled"
    [text] = messages_to(telegram, admin_chat)
    assert "Google Drive is disconnected" in text


@pytest.mark.parametrize("failure", ["http_500", "network"])
async def test_notification_failure_never_changes_the_job(
    queue: JobQueue,
    sessions: Sessions,
    notifier: Notifier,
    telegram: respx.Route,
    admin_chat: int,
    tmp_path: Path,
    failure: str,
) -> None:
    if failure == "http_500":
        telegram.respond(500, json={"ok": False})
    else:
        telegram.side_effect = httpx.ConnectError("down")

    job_id, status = await run_job(queue, sessions, ClipStage(sessions), notifier, tmp_path)

    assert status == "completed"
    assert (await load_job(sessions, job_id)).status == "completed"
    assert telegram.called


async def test_disabled_notifications_send_nothing(
    queue: JobQueue,
    sessions: Sessions,
    notifier: Notifier,
    telegram: respx.Route,
    admin_chat: int,
    tmp_path: Path,
) -> None:
    async with sessions.begin() as session:
        await session.execute(insert(AppSetting).values(key=NOTIFICATIONS_KEY, value=False))

    _, status = await run_job(queue, sessions, ClipStage(sessions), notifier, tmp_path)
    await notifier.discovery_summary(DiscoveryReport(sources_run=1))

    assert status == "completed"
    assert not telegram.called


async def test_discovery_summary_is_sent(
    notifier: Notifier, telegram: respx.Route, admin_chat: int
) -> None:
    await notifier.discovery_summary(DiscoveryReport(sources_run=2, new_videos=5))

    [text] = messages_to(telegram, admin_chat)
    assert "New videos: 5" in text
