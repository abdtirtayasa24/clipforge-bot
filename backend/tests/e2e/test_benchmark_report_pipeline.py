from datetime import timedelta
from pathlib import Path

from clipforge.tools import benchmark_report
from clipforge.tools.benchmark_report import STAGES
from tests.jobs import claim
from tests.pipeline import Pipeline


async def test_report_measures_a_real_pipeline_run(
    pipeline: Pipeline, migrated_database_url: str, tmp_path: Path
) -> None:
    since = pipeline.clock() - timedelta(seconds=1)
    job_id = await pipeline.enqueue()
    assert await pipeline.runner().run(await claim(pipeline.queue)) == "completed"

    async with pipeline.sessions() as session:
        report = await benchmark_report.load_report(session, since=since, limit=50)

    [run] = [run for run in report.jobs if run.id == job_id]
    assert set(run.stage_seconds) == set(STAGES)
    assert all(seconds >= 0 for seconds in run.stage_seconds.values())
    assert run.clips == 2
    assert set(run.costs) == {"USD"}
    assert report.requests_per_job, "AI calls are counted per model"

    resources = tmp_path / "resources.log"
    resources.write_text("10:00:00 mem=1 peak=1 free=99999999999\n", encoding="utf-8")
    output = tmp_path / "report.md"
    args = benchmark_report._parser().parse_args(
        ["--since", since.isoformat(), "--resources", str(resources), "--output", str(output)]
    )

    assert await benchmark_report._run(args, migrated_database_url) == benchmark_report.EXIT_OK
    text = output.read_text(encoding="utf-8")
    assert "## Per-stage latency (seconds)" in text
    assert "Peak worker memory" in text
    assert "Recommended `daily_job_cap`" in text
