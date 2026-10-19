import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from clipforge.api.deps import SettingsDep
from clipforge.api.errors import Conflict
from clipforge.auth.deps import require_admin
from clipforge.discovery.service import DiscoveryAlreadyRunning, run_exclusive

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/discovery", tags=["discovery"], dependencies=[Depends(require_admin)])


def _log_outcome(task: "asyncio.Task[object]") -> None:
    if task.cancelled():
        return
    error = task.exception()
    if error is not None and not isinstance(error, DiscoveryAlreadyRunning):
        logger.error("manual discovery failed", exc_info=error)


@router.post("/run", status_code=status.HTTP_202_ACCEPTED)
async def run_discovery(request: Request, settings: SettingsDep) -> dict[str, str]:
    engine = request.app.state.engine
    if engine is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "database is not configured")
    if settings.youtube_api_key is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "YouTube API key is not configured"
        )

    lock_state: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
    task = asyncio.create_task(
        run_exclusive(
            engine, settings.youtube_api_key.get_secret_value(), on_lock=lock_state.set_result
        )
    )
    background: set[asyncio.Task[object]] = request.app.state.background_tasks
    background.add(task)
    task.add_done_callback(background.discard)
    task.add_done_callback(_log_outcome)

    first_done: list[asyncio.Future[Any]] = [lock_state, task]
    await asyncio.wait(first_done, return_when=asyncio.FIRST_COMPLETED)
    if not lock_state.done():
        task.result()
    if not lock_state.result():
        raise Conflict("discovery is already running")
    logger.info("manual discovery started")
    return {"status": "started"}
