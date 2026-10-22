import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clipforge.affiliate.matcher import AffiliateMatcher, EmbeddingSetup
from clipforge.ai.registry import ProviderConfigError, ProviderRegistry
from clipforge.ai.usage import usage_recorder
from clipforge.db.app_settings import get_setting
from clipforge.db.models import Clip
from clipforge.pipeline.context import StageContext
from clipforge.pipeline.stage import StageResult
from clipforge.settings_schema import JobSettings

logger = logging.getLogger(__name__)

AFFILIATE_TOGGLE_KEY = "affiliate_links_enabled"


async def affiliate_links_enabled(session: AsyncSession) -> bool:
    """Read live, not from the job snapshot: the switch applies the moment it is flipped."""
    return await get_setting(session, AFFILIATE_TOGGLE_KEY) is True


class AffiliateStage:
    """Suggests catalog products per clip when affiliate links are enabled (ADR-020)."""

    name: str = "affiliate_matching"
    running_name: str | None = "matching_affiliates"

    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], registry: ProviderRegistry
    ) -> None:
        self._sessions = sessions
        self._registry = registry

    def _embedding(self, ctx: StageContext) -> EmbeddingSetup | None:
        snapshot = JobSettings.of(ctx.job.settings_snapshot).providers.get("embedding")
        if snapshot is None:
            return None
        try:
            provider = self._registry.embedding(snapshot)
        except ProviderConfigError as exc:
            logger.warning("affiliate embeddings unavailable", extra={"reason": str(exc)})
            return None
        record = usage_recorder(
            self._sessions, provider=snapshot, job_id=ctx.job.lease.job_id, stage=self.name
        )
        return EmbeddingSetup(provider, snapshot.model, record)

    async def run(self, ctx: StageContext) -> StageResult:
        async with self._sessions() as session:
            if not await affiliate_links_enabled(session):
                return StageResult(details={"enabled": False})
            clip_ids = list(
                await session.scalars(
                    select(Clip.id).where(Clip.job_id == ctx.job.lease.job_id).order_by(Clip.id)
                )
            )
        matcher = AffiliateMatcher(self._sessions, self._embedding(ctx))
        suggestions = 0
        for clip_id in clip_ids:
            await ctx.check_cancelled()
            # A clip with links was matched before a crash, or already reviewed: keep it.
            if not await matcher.has_links(clip_id):
                suggestions += await matcher.match_clip(clip_id)
        return StageResult(details={"enabled": True, "suggestions": suggestions})
