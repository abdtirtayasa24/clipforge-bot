import asyncio
import logging
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clipforge.ai.registry import ProviderConfigError, missing_secrets, resolve_providers
from clipforge.api.deps import SettingsDep
from clipforge.db.models import AppSetting
from clipforge.settings_schema import load_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["health"])

DEFAULT_RESERVED_DISK_BYTES = 5 * 1024**3
DATABASE_CHECK_TIMEOUT_S = 2.0
MEDIA_BINARIES = ("ffmpeg", "ffprobe")


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


async def _check_database(
    sessions: async_sessionmaker[AsyncSession] | None,
) -> tuple[bool, int]:
    """Returns (reachable, reserved disk bytes from app_settings)."""
    if sessions is None:
        return False, DEFAULT_RESERVED_DISK_BYTES
    try:
        async with asyncio.timeout(DATABASE_CHECK_TIMEOUT_S), sessions() as session:
            await session.execute(text("SELECT 1"))
            reserve = await session.scalar(
                select(AppSetting.value).where(AppSetting.key == "reserved_disk_bytes")
            )
    except Exception:
        logger.warning("readiness database check failed", exc_info=True)
        return False, DEFAULT_RESERVED_DISK_BYTES
    return True, reserve if isinstance(reserve, int) else DEFAULT_RESERVED_DISK_BYTES


async def _check_ai_providers(sessions: async_sessionmaker[AsyncSession] | None) -> bool:
    """Every selected model is configured and its secret env var is set."""
    if sessions is None:
        return False
    try:
        async with asyncio.timeout(DATABASE_CHECK_TIMEOUT_S), sessions() as session:
            providers = await resolve_providers(session, (await load_settings(session)).models)
    except (ProviderConfigError, ValidationError) as exc:
        logger.warning("AI provider configuration is invalid", extra={"reason": str(exc)})
        return False
    except Exception:
        logger.warning("readiness AI provider check failed", exc_info=True)
        return False
    missing = missing_secrets(providers)
    if missing:
        logger.warning("AI provider secrets are not set", extra={"missing_env_vars": missing})
    return not missing


def _check_workspace(workspace: Path) -> bool:
    try:
        with tempfile.TemporaryFile(dir=workspace):
            return True
    except OSError:
        return False


def _check_disk(workspace: Path, reserved_bytes: int) -> bool:
    try:
        return shutil.disk_usage(workspace).free >= reserved_bytes
    except OSError:
        return False


def _check_media_binaries() -> bool:
    return all(shutil.which(name) for name in MEDIA_BINARIES)


@router.get("/ready")
async def ready(request: Request, settings: SettingsDep) -> JSONResponse:
    database_ok, reserved_bytes = await _check_database(request.app.state.session_factory)
    workspace = settings.workspace_dir
    checks = {
        "database": database_ok,
        "workspace": await asyncio.to_thread(_check_workspace, workspace),
        "disk": await asyncio.to_thread(_check_disk, workspace, reserved_bytes),
        "ffmpeg": _check_media_binaries(),
        "ai_providers": await _check_ai_providers(request.app.state.session_factory),
    }
    failing = [name for name, ok in checks.items() if not ok]
    if failing:
        logger.warning("not ready", extra={"failing": failing})
    return JSONResponse(
        {
            "status": "unavailable" if failing else "ok",
            "failing": failing,
            "checks": {name: "ok" if ok else "fail" for name, ok in checks.items()},
        },
        status_code=503 if failing else 200,
    )
