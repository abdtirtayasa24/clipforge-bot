from pathlib import Path

import pytest

from clipforge.pipeline.stages import render
from tests.jobs import claim, event_types, load_job
from tests.pipeline import Pipeline


async def test_cancel_mid_render_stops_after_the_current_clip(
    pipeline: Pipeline, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = await pipeline.enqueue()
    render_clip = render.render_clip

    async def render_then_cancel(*args: object, **kwargs: object) -> None:
        await render_clip(*args, **kwargs)  # type: ignore[arg-type]
        await pipeline.queue.request_cancel(job_id)

    monkeypatch.setattr(render, "render_clip", render_then_cancel)

    status = await pipeline.runner().run(await claim(pipeline.queue))

    job = await load_job(pipeline.sessions, job_id)
    assert status == "cancelled"
    assert (job.status, job.stage, job.lease_token) == ("cancelled", "candidates_selected", None)
    assert (await event_types(pipeline.sessions, job_id))[-2:] == ["cancel_requested", "cancelled"]
    [(_, artifact)] = await pipeline.clips(job_id)
    assert artifact.upload_state == "pending"
    assert pipeline.drive.requests == []
    assert not list(Path(pipeline.workspace).rglob("*.part.mp4"))
    assert not any(text.startswith("ClipForge: job failed") for text in pipeline.messages())
