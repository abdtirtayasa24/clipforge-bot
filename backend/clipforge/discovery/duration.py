import re

_DURATION = re.compile(
    r"^P(?:(?P<weeks>\d+)W)?(?:(?P<days>\d+)D)?"
    r"(?:T(?=\d)(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)
_SECONDS = {"weeks": 604800, "days": 86400, "hours": 3600, "minutes": 60, "seconds": 1}


def parse_iso8601_duration(value: str) -> int:
    """Seconds in a YouTube `contentDetails.duration`, e.g. PT1H2M3S; live streams are P0D."""
    match = _DURATION.match(value)
    if match is None or value in ("P", "PT"):
        raise ValueError(f"invalid ISO-8601 duration: {value!r}")
    return sum(int(amount) * _SECONDS[unit] for unit, amount in match.groupdict().items() if amount)
