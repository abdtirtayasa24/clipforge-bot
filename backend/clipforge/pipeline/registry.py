"""The production pipeline: stage order and the real AI adapters (ADR-006, ADR-009)."""

from pathlib import Path

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clipforge.ai.adapters.gemini_analysis import gemini_analysis_factory
from clipforge.ai.adapters.gemini_transcription import (
    gemini_audio_segments_factory,
    gemini_transcription_factory,
)
from clipforge.ai.adapters.openrouter_analysis import openrouter_analysis_factory
from clipforge.ai.quota import QuotaGuard
from clipforge.ai.registry import ProviderRegistry
from clipforge.config import Settings
from clipforge.pipeline.stage import Stage
from clipforge.pipeline.stages.analyze import AnalyzeStage
from clipforge.pipeline.stages.audio import AudioStage
from clipforge.pipeline.stages.ingest import IngestStage
from clipforge.pipeline.stages.render import RenderStage
from clipforge.pipeline.stages.select import SelectStage
from clipforge.pipeline.stages.transcribe import TranscribeStage
from clipforge.pipeline.stages.upload import UploadStage
from clipforge.security.crypto import TokenCipher
from clipforge.storage.drive import DriveClient
from clipforge.storage.drive_oauth import DriveTokens, OAuthClient


def provider_registry(http: httpx.AsyncClient, quota: QuotaGuard) -> ProviderRegistry:
    return ProviderRegistry(
        transcription={"gemini": gemini_transcription_factory(http, quota)},
        transcription_fallback={"gemini": gemini_audio_segments_factory(http, quota)},
        analysis={
            "gemini": gemini_analysis_factory(http, quota),
            "openrouter": openrouter_analysis_factory(http, quota),
        },
    )


def drive_client(
    settings: Settings, http: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> DriveClient | None:
    """None until the Google OAuth client and encryption key are configured."""
    if not (
        settings.google_client_id
        and settings.google_client_secret
        and settings.drive_redirect_uri
        and settings.token_encryption_key
    ):
        return None
    client = OAuthClient(
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret.get_secret_value(),
        redirect_uri=settings.drive_redirect_uri,
    )
    cipher = TokenCipher(settings.token_encryption_key.get_secret_value())
    return DriveClient(http, DriveTokens(sessions, http, client, cipher), sessions)


def default_stages(
    sessions: async_sessionmaker[AsyncSession],
    *,
    workspace_root: Path,
    registry: ProviderRegistry,
    quota: QuotaGuard,
    drive: DriveClient | None,
) -> list[Stage]:
    return [
        IngestStage(sessions, workspace_root=workspace_root),
        AudioStage(sessions),
        TranscribeStage(sessions, registry, quota),
        AnalyzeStage(sessions, registry, quota),
        SelectStage(sessions),
        RenderStage(sessions),
        UploadStage(sessions, drive),
    ]
