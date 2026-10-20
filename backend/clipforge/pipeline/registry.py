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
from clipforge.pipeline.stage import Stage
from clipforge.pipeline.stages.analyze import AnalyzeStage
from clipforge.pipeline.stages.audio import AudioStage
from clipforge.pipeline.stages.ingest import IngestStage
from clipforge.pipeline.stages.select import SelectStage
from clipforge.pipeline.stages.transcribe import TranscribeStage


def provider_registry(http: httpx.AsyncClient, quota: QuotaGuard) -> ProviderRegistry:
    return ProviderRegistry(
        transcription={"gemini": gemini_transcription_factory(http, quota)},
        transcription_fallback={"gemini": gemini_audio_segments_factory(http, quota)},
        analysis={
            "gemini": gemini_analysis_factory(http, quota),
            "openrouter": openrouter_analysis_factory(http, quota),
        },
    )


def default_stages(
    sessions: async_sessionmaker[AsyncSession],
    *,
    workspace_root: Path,
    registry: ProviderRegistry,
    quota: QuotaGuard,
) -> list[Stage]:
    return [
        IngestStage(sessions, workspace_root=workspace_root),
        AudioStage(sessions),
        TranscribeStage(sessions, registry, quota),
        AnalyzeStage(sessions, registry, quota),
        SelectStage(sessions),
    ]
