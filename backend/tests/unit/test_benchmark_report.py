import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from clipforge.tools.benchmark_report import (
    JobRun,
    Marker,
    Report,
    Resources,
    capacity_by_quota,
    capacity_by_time,
    parse_resources,
    recommended_cap,
    render,
    stage_seconds,
)

T0 = datetime(2026, 10, 21, 8, tzinfo=UTC)
GIB = 1024**3


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def job(wall_minutes: float, *, clips: int = 3) -> JobRun:
    return JobRun(
        id=uuid.uuid4(),
        status="completed",
        started_at=T0,
        finished_at=at(wall_minutes * 60),
        clips=clips,
        stage_seconds={"transcribed": 120.0, "uploading": 30.0},
        costs={"USD": Decimal("0.01")},
    )


def resources(*, peak: int = GIB, min_free: int = 10 * GIB) -> Resources:
    return Resources(
        samples=10,
        peak_memory_bytes=peak,
        min_free_bytes=min_free,
        peak_disk_used_bytes=2 * GIB,
        cpu_seconds=600.0,
        wall_seconds=1200.0,
    )


def test_stage_time_runs_from_the_previous_marker_of_the_same_attempt() -> None:
    markers = [
        Marker("created", "queued", at(0)),
        Marker("claimed", "queued", at(10)),
        Marker("stage_changed", "ingesting", at(11)),
        Marker("stage_changed", "validated", at(20)),
        Marker("stage_changed", "audio_extracted", at(50)),
        Marker("retry_scheduled", "audio_extracted", at(80)),
        # The wait between attempts is not part of any stage.
        Marker("claimed", "audio_extracted", at(1000)),
        Marker("stage_changed", "transcribed", at(1100)),
        Marker("completed", "completed", at(1101)),
    ]

    assert stage_seconds(markers) == {
        "validated": 9.0,
        "audio_extracted": 30.0,
        "transcribed": 100.0,
    }


def test_resource_log_gives_peaks_disk_use_and_cpu_share() -> None:
    lines = [
        "10:00:00 mem=100 peak=100 free=5000 cpu=0",
        "garbage line",
        "10:00:10 mem=900 peak=950 free=4200 cpu=4000000000",
        "10:00:20 mem=300 peak=950 free=4800 cpu=9000000000",
    ]

    parsed = parse_resources(lines)

    assert parsed == Resources(
        samples=3,
        peak_memory_bytes=950,
        min_free_bytes=4200,
        peak_disk_used_bytes=800,
        cpu_seconds=9.0,
        wall_seconds=20.0,
    )
    assert parse_resources(["nothing useful"]) is None


def test_capacity_uses_the_p90_wall_time_and_the_tightest_daily_quota() -> None:
    report = Report(
        jobs=[job(30) for _ in range(9)] + [job(96)],
        requests_per_job={"gemini:flash": 20.0, "gemini:pro": 2.0},
        rpd_limits={"gemini:flash": 250, "gemini:pro": 100},
    )

    # p90 of the ten wall times is 30 min: 86400 * 0.8 / 1800 = 38 videos/day.
    assert capacity_by_time(report) == 38
    assert capacity_by_quota(report) == 12
    assert recommended_cap(report) == 10


def test_cap_never_exceeds_what_the_measurements_sustain() -> None:
    slow = Report(jobs=[job(400)])
    assert capacity_by_time(slow) == 2
    assert recommended_cap(slow) == 2

    fast = Report(jobs=[job(20)], resources=resources())
    assert recommended_cap(fast) == 20

    tight_disk = Report(jobs=[job(20)], resources=resources(min_free=4 * GIB))
    assert recommended_cap(tight_disk) == 5


def test_report_lists_jobs_stages_resources_and_the_recommendation() -> None:
    report = Report(
        jobs=[job(30), job(40, clips=2)],
        requests_per_job={"gemini:flash": 20.0},
        rpd_limits={"gemini:flash": 1000},
        resources=resources(),
    )

    text = render(report)

    assert "| transcribed | 120.0 | 120.0 | 120.0 |" in text
    assert "Estimated AI cost per job: 0.0100 USD" in text
    assert "AI requests per job, gemini:flash: 20.0 (RPD limit 1000)" in text
    assert "Peak worker memory: 1.00 GiB" in text
    assert "Worker CPU: 600 s, 25% of 2 vCPUs" in text
    assert "- 20/day: sustainable, AI ~0.2000 USD/day" in text
    assert "**Recommended `daily_job_cap`: 20**" in text
