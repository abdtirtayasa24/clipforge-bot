"""The production pipeline with fake AI providers, an in-memory Drive and a captured Telegram
chat: each reliability scenario must end recovered or visibly failed, never silently."""

import json
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import psycopg
import pytest
import respx
from sqlalchemy import delete, insert, select

from clipforge.ai.adapters.fake import FakeAnalysisProvider, FakeTranscriptionProvider
from clipforge.ai.quota import QuotaGuard
from clipforge.ai.registry import ProviderRegistry, snapshot_job_settings
from clipforge.ai.types import ClipCandidate, DimensionScores, TranscriptResult
from clipforge.ai.usage import UsageRecorder
from clipforge.checksums import file_sha256
from clipforge.db.models import AppSetting, Clip, ClipArtifact, MediaAsset, Video
from clipforge.media.probe import probe
from clipforge.notify.telegram import TelegramClient
from clipforge.pipeline.registry import default_stages
from clipforge.pipeline.runner import PipelineRunner
from clipforge.pipeline.stage import Stage
from clipforge.pipeline.stages.notify import NOTIFICATIONS_KEY, Notifier
from clipforge.queue.repository import JobQueue
from clipforge.settings_schema import ProviderSnapshot
from clipforge.storage.drive import CLIP_ID_PROPERTY, DriveClient
from clipforge.storage.drive_oauth import FOLDER_KEY
from tests.clock import FakeClock
from tests.db import libpq_url
from tests.drive import FakeDrive, FakeTokens
from tests.jobs import Sessions, event_types, load_job

BOT_TOKEN = "123456:RELIABILITY-token"
SEND_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
FINAL_STATUSES = {"completed", "completed_no_clips", "failed", "cancelled"}


def candidate(start: float, title: str) -> ClipCandidate:
    level = 90
    return ClipCandidate(
        start_s=start,
        end_s=start + 30,
        scores=DimensionScores(
            hook=level,
            emotion=level,
            clarity=level,
            relevance=level,
            visual=level,
            completeness=level,
        ),
        title=title,
        hook_line=f"Pembuka {title}",
        explanation="uji keandalan",
        topic=f"Topik {title}",
        keywords=[title.lower()],
    )


PROPOSALS = [candidate(100, "Satu"), candidate(700, "Dua")]


class FlakyTranscription(FakeTranscriptionProvider):
    """Raises the queued errors, one per call, before answering like the fake provider."""

    def __init__(self) -> None:
        super().__init__()
        self.failures: list[Exception] = []

    async def transcribe(
        self,
        audio_path: Path,
        *,
        duration_s: float,
        language_hint: str | None,
        record: UsageRecorder,
    ) -> TranscriptResult:
        if self.failures:
            raise self.failures.pop(0)
        return await super().transcribe(
            audio_path, duration_s=duration_s, language_hint=language_hint, record=record
        )


@dataclass
class Pipeline:
    sessions: Sessions
    clock: FakeClock
    queue: JobQueue
    drive: FakeDrive
    telegram: respx.Route
    http: httpx.AsyncClient
    workspace: Path
    source: Path
    admin_chat: int
    transcription: FlakyTranscription = field(default_factory=FlakyTranscription)
    analysis: FakeAnalysisProvider = field(default_factory=lambda: FakeAnalysisProvider(PROPOSALS))

    def registry(self) -> ProviderRegistry:
        def fixed[T](provider: T) -> Callable[[ProviderSnapshot, str], T]:
            return lambda snapshot, key: provider

        return ProviderRegistry(
            transcription={"gemini": fixed(self.transcription)},
            analysis={"gemini": fixed(self.analysis)},
            environ={"GEMINI_API_KEY": "test", "OPENROUTER_API_KEY": "test"},
        )

    def drive_client(self) -> DriveClient:
        return DriveClient(self.http, FakeTokens(), self.sessions)

    def stages(self, drive: DriveClient | None = None) -> list[Stage]:
        return default_stages(
            self.sessions,
            workspace_root=self.workspace,
            registry=self.registry(),
            quota=QuotaGuard(self.sessions, clock=self.clock),
            drive=drive or self.drive_client(),
        )

    def runner(
        self, stages: list[Stage] | None = None, *, queue: JobQueue | None = None
    ) -> PipelineRunner:
        return PipelineRunner(
            queue or self.queue,
            stages or self.stages(),
            workspace_root=self.workspace,
            notifier=Notifier(self.sessions, TelegramClient(self.http, BOT_TOKEN)),
        )

    async def enqueue(self, **asset: object) -> uuid.UUID:
        """A job for an uploaded copy of the shared source unless `asset` overrides it."""
        async with self.sessions.begin() as session:
            video_id = await session.scalar(
                insert(Video)
                .values(
                    youtube_video_id=uuid.uuid4().hex[:11],
                    title="Uji keandalan",
                    channel_id="UCreliability",
                    default_language="id",
                )
                .returning(Video.id)
            )
            values: dict[str, object] = {
                "video_id": video_id,
                "kind": "upload",
                "permission_evidence": {"type": "own_content"},
                "state": "ready",
                "local_path": str(self.source),
                "sha256": file_sha256(self.source),
                "media_info": (await probe(self.source)).model_dump(mode="json"),
            }
            asset_id = await session.scalar(
                insert(MediaAsset).values(values | asset).returning(MediaAsset.id)
            )
        assert video_id is not None and asset_id is not None
        job, _ = await self.queue.enqueue(video_id, uuid.uuid4().hex, media_asset_id=asset_id)
        return job.id

    def messages(self) -> list[str]:
        bodies = [json.loads(call.request.content) for call in self.telegram.calls]
        return [body["text"] for body in bodies if body["chat_id"] == self.admin_chat]

    async def clips(self, job_id: uuid.UUID) -> list[tuple[Clip, ClipArtifact]]:
        async with self.sessions() as session:
            rows = await session.execute(
                select(Clip, ClipArtifact)
                .join(ClipArtifact, ClipArtifact.clip_id == Clip.id)
                .where(Clip.job_id == job_id, ClipArtifact.kind == "video")
            )
            return [(clip, artifact) for clip, artifact in rows]

    async def assert_no_duplicate_uploads(self, job_id: uuid.UUID) -> None:
        async with self.sessions() as session:
            clip_ids = list(await session.scalars(select(Clip.id).where(Clip.job_id == job_id)))
        for clip_id in clip_ids:
            assert len(self.drive.live_files(**{CLIP_ID_PROPERTY: str(clip_id)})) <= 1

    async def assert_recovered(self, job_id: uuid.UUID, *, clips: int = len(PROPOSALS)) -> None:
        """Completed with every clip verified on Drive exactly once."""
        job = await load_job(self.sessions, job_id)
        assert (job.status, job.lease_token) == ("completed", None)
        rows = await self.clips(job_id)
        assert len(rows) == clips
        for clip, artifact in rows:
            [remote] = self.drive.live_files(**{CLIP_ID_PROPERTY: str(clip.id)})
            assert (artifact.upload_state, clip.drive_file_id) == ("verified", remote["id"])
            assert remote["md5Checksum"] == artifact.md5
        assert any("clips ready for review" in text for text in self.messages())

    async def assert_failed_visibly(self, job_id: uuid.UUID, reason: str) -> None:
        """Failed with a `failed` event and a Telegram message that names the reason."""
        job = await load_job(self.sessions, job_id)
        assert job.status == "failed"
        assert job.last_error and reason in job.last_error
        assert (await event_types(self.sessions, job_id))[-1] == "failed"
        failures = [text for text in self.messages() if text.startswith("ClipForge: job failed")]
        assert len(failures) == 1 and reason in failures[0]
        await self.assert_no_duplicate_uploads(job_id)


@pytest.fixture
def admin_chat(migrated_database_url: str) -> Iterator[int]:
    """Short connections: a scenario may terminate every open connection to the database."""
    chat_id = int(uuid.uuid4().int % 10**9) + 1
    url = libpq_url(migrated_database_url)
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DELETE FROM app_settings WHERE key = %s", (NOTIFICATIONS_KEY,))
        conn.execute("INSERT INTO admin_users (telegram_user_id) VALUES (%s)", (chat_id,))
    yield chat_id
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DELETE FROM admin_users WHERE telegram_user_id = %s", (chat_id,))


@pytest.fixture
def network() -> Iterator[tuple[FakeDrive, respx.Route]]:
    """Drive and Telegram are faked; any other outbound request fails the test."""
    drive = FakeDrive()
    with respx.mock(assert_all_called=False) as router:
        router.route(host="www.googleapis.com").mock(side_effect=drive.handle)
        telegram = router.post(SEND_URL).respond(json={"ok": True, "result": {}})
        yield drive, telegram


@pytest.fixture
async def pipeline(
    sessions: Sessions,
    clock: FakeClock,
    network: tuple[FakeDrive, respx.Route],
    admin_chat: int,
    valid_source: Path,
    tmp_path: Path,
) -> AsyncIterator[Pipeline]:
    async with sessions.begin() as session:
        await session.execute(delete(AppSetting).where(AppSetting.key == FOLDER_KEY))
    drive, telegram = network
    queue = JobQueue(
        sessions, clock=clock, jitter=lambda: 0.0, snapshot_settings=snapshot_job_settings
    )
    async with httpx.AsyncClient() as http:
        yield Pipeline(
            sessions=sessions,
            clock=clock,
            queue=queue,
            drive=drive,
            telegram=telegram,
            http=http,
            workspace=tmp_path / "workspace",
            source=valid_source,
            admin_chat=admin_chat,
        )
    async with sessions.begin() as session:
        await session.execute(delete(AppSetting).where(AppSetting.key == FOLDER_KEY))
