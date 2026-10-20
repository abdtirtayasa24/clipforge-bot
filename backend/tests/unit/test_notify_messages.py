from decimal import Decimal

from clipforge.discovery.service import DiscoveryReport
from clipforge.pipeline.stages.notify import (
    DRIVE_DISCONNECTED,
    ReadyClip,
    clips_ready_message,
    discovery_message,
    job_failed_message,
)


def test_clips_ready_lists_titles_scores_and_cost() -> None:
    text = clips_ready_message(
        "Diskusi teknologi",
        [ReadyClip("Masa depan AI", 88), ReadyClip("Kerja jarak jauh", 81)],
        {"USD": Decimal("0.01234567")},
    )

    assert text.splitlines() == [
        "ClipForge: 2 clips ready for review",
        "Video: Diskusi teknologi",
        "1. Masa depan AI (score 88)",
        "2. Kerja jarak jauh (score 81)",
        "Estimated AI cost: 0.0123 USD",
    ]


def test_free_job_reports_zero_cost() -> None:
    text = clips_ready_message("v", [ReadyClip("t", 90)], {})

    assert text.endswith("Estimated AI cost: 0.0000 USD")


def test_failure_reason_is_redacted_and_shortened() -> None:
    reason = "fetch https://host/x?token=abc123 failed; api_key=s3cret " + "x" * 1000

    text = job_failed_message("Video", "uploading", reason)

    assert text.startswith("ClipForge: job failed\nVideo: Video\nStage: uploading\nReason: ")
    assert "abc123" not in text and "s3cret" not in text
    assert len(text) < 600


def test_discovery_summary() -> None:
    report = DiscoveryReport(
        sources_run=3, failed_sources=["Kanal A"], new_videos=4, updated_videos=2
    )

    assert discovery_message(report).splitlines() == [
        "ClipForge: discovery finished",
        "Sources run: 3 (1 failed: Kanal A)",
        "New videos: 4, updated: 2",
    ]


def test_messages_carry_no_links() -> None:
    texts = [
        clips_ready_message("v", [ReadyClip("t", 90)], {}),
        job_failed_message("v", None, "boom"),
        discovery_message(DiscoveryReport()),
        DRIVE_DISCONNECTED,
    ]

    assert not any("http" in text for text in texts)
