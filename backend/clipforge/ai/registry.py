"""Resolves selected models to provider configs and builds adapters for a job (ADR-009)."""

import os
from collections.abc import Callable, Mapping
from typing import Any, cast, get_args

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clipforge.ai.protocols import (
    EmbeddingProvider,
    TranscriptionProvider,
    ViralAnalysisProvider,
)
from clipforge.db.models import AiProviderConfig
from clipforge.queue.errors import PermanentError
from clipforge.settings_schema import (
    JobSettings,
    ModelRole,
    ModelSelection,
    PriceSnapshot,
    ProviderSnapshot,
    QuotaReset,
    load_settings,
)

KNOWN_PROVIDERS = ("gemini", "openrouter")


class ProviderConfigError(PermanentError):
    """A selected model is not configured, is disabled, or has no secret in the environment."""


def parse_selection(value: str) -> tuple[str | None, str]:
    """`provider:model` or a bare `model` (model IDs may themselves contain colons)."""
    head, separator, rest = value.partition(":")
    if separator and head in KNOWN_PROVIDERS:
        return head, rest
    return None, value


def capability_of(role: ModelRole) -> str:
    return role.removesuffix("_fallback")


def snapshot_of(config: AiProviderConfig) -> ProviderSnapshot:
    return ProviderSnapshot(
        provider=config.provider,
        model=config.model,
        capability=config.capability,
        secret_ref=config.secret_ref,
        rpm_limit=config.rpm_limit,
        rpd_limit=config.rpd_limit,
        quota_reset=cast(QuotaReset, config.quota_reset),
        price=PriceSnapshot(
            input_per_mtok=config.price_input_per_mtok,
            output_per_mtok=config.price_output_per_mtok,
            per_audio_minute=config.price_per_audio_minute,
            currency=config.currency,
        ),
    )


async def resolve_providers(
    session: AsyncSession, models: ModelSelection
) -> dict[ModelRole, ProviderSnapshot]:
    resolved: dict[ModelRole, ProviderSnapshot] = {}
    for role in get_args(ModelRole):
        value = getattr(models, role)
        if value is None:
            continue
        provider, model = parse_selection(value)
        query = select(AiProviderConfig).where(
            AiProviderConfig.model == model,
            AiProviderConfig.capability == capability_of(role),
            AiProviderConfig.enabled,
        )
        if provider:
            query = query.where(AiProviderConfig.provider == provider)
        configs = list(await session.scalars(query.order_by(AiProviderConfig.provider)))
        if not configs:
            raise ProviderConfigError(f"{role} model {value!r} is not configured or is disabled")
        resolved[role] = snapshot_of(configs[0])
    return resolved


async def snapshot_job_settings(session: AsyncSession) -> dict[str, Any]:
    settings = await load_settings(session)
    providers = await resolve_providers(session, settings.models)
    return JobSettings(**dict(settings), providers=providers).model_dump(mode="json")


def missing_secrets(
    providers: Mapping[ModelRole, ProviderSnapshot], environ: Mapping[str, str] = os.environ
) -> list[str]:
    """Names (never values) of secret env vars the selected providers need but lack."""
    return sorted({p.secret_ref for p in providers.values() if not environ.get(p.secret_ref)})


class ProviderRegistry:
    """Adapter factories per provider name; a factory gets the snapshot and its API key."""

    def __init__(
        self,
        *,
        transcription: Mapping[str, Callable[[ProviderSnapshot, str], TranscriptionProvider]]
        | None = None,
        transcription_fallback: Mapping[
            str, Callable[[ProviderSnapshot, str], TranscriptionProvider]
        ]
        | None = None,
        analysis: Mapping[str, Callable[[ProviderSnapshot, str], ViralAnalysisProvider]]
        | None = None,
        embedding: Mapping[str, Callable[[ProviderSnapshot, str], EmbeddingProvider]] | None = None,
        environ: Mapping[str, str] = os.environ,
    ) -> None:
        self._transcription = dict(transcription or {})
        self._transcription_fallback = dict(transcription_fallback or {})
        self._analysis = dict(analysis or {})
        self._embedding = dict(embedding or {})
        self._environ = environ

    def _secret(self, snapshot: ProviderSnapshot) -> str:
        secret = self._environ.get(snapshot.secret_ref)
        if not secret:
            raise ProviderConfigError(f"environment variable {snapshot.secret_ref} is not set")
        return secret

    @staticmethod
    def _factory[T](factories: Mapping[str, T], snapshot: ProviderSnapshot) -> T:
        try:
            return factories[snapshot.provider]
        except KeyError:
            raise ProviderConfigError(
                f"no {snapshot.capability} adapter for provider {snapshot.provider!r}"
            ) from None

    def transcription(self, snapshot: ProviderSnapshot) -> TranscriptionProvider:
        return self._factory(self._transcription, snapshot)(snapshot, self._secret(snapshot))

    def transcription_fallback(self, snapshot: ProviderSnapshot) -> TranscriptionProvider:
        """Segment-level transcription through a multimodal model (ADR-027)."""
        factory = self._factory(self._transcription_fallback, snapshot)
        return factory(snapshot, self._secret(snapshot))

    def analysis(self, snapshot: ProviderSnapshot) -> ViralAnalysisProvider:
        return self._factory(self._analysis, snapshot)(snapshot, self._secret(snapshot))

    def embedding(self, snapshot: ProviderSnapshot) -> EmbeddingProvider:
        return self._factory(self._embedding, snapshot)(snapshot, self._secret(snapshot))
