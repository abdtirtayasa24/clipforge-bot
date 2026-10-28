from datetime import datetime

import pytest

from clipforge.discovery.service import DiscoverySettings
from clipforge.discovery_cli import scheduled_run_due


@pytest.mark.parametrize(
    ("auto_enabled", "auto_hour", "now", "due"),
    [
        (True, 3, datetime(2026, 10, 28, 3, 0), True),
        (True, 3, datetime(2026, 10, 28, 3, 59), True),
        (True, 3, datetime(2026, 10, 28, 4, 0), False),
        (True, 0, datetime(2026, 10, 28, 0, 5), True),
        (True, 23, datetime(2026, 10, 28, 22, 59), False),
        (False, 3, datetime(2026, 10, 28, 3, 0), False),
    ],
)
def test_scheduled_run_is_due_only_in_the_enabled_hour(
    auto_enabled: bool, auto_hour: int, now: datetime, due: bool
) -> None:
    discovery = DiscoverySettings(auto_enabled=auto_enabled, auto_hour=auto_hour)

    assert scheduled_run_due(discovery, now) is due
