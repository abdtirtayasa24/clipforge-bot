import pytest

from clipforge.discovery.duration import parse_iso8601_duration


@pytest.mark.parametrize(
    ("value", "seconds"),
    [
        ("PT45M", 2700),
        ("PT1H2M3S", 3723),
        ("PT2H", 7200),
        ("PT59S", 59),
        ("P1DT1M", 86460),
        ("P0D", 0),
        ("PT0S", 0),
    ],
)
def test_parses_youtube_durations(value: str, seconds: int) -> None:
    assert parse_iso8601_duration(value) == seconds


@pytest.mark.parametrize("value", ["", "P", "PT", "45M", "PT1.5H", "PT-1M", "P1Y", "PT1H2X"])
def test_rejects_invalid_durations(value: str) -> None:
    with pytest.raises(ValueError):
        parse_iso8601_duration(value)
