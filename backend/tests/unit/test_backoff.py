from datetime import timedelta

import pytest

from clipforge.queue.backoff import retry_delay


@pytest.mark.parametrize(
    ("attempt", "minutes"),
    [(1, 1), (2, 5), (3, 15), (4, 15), (9, 15)],
)
def test_delays_follow_one_five_fifteen_minutes(attempt: int, minutes: int) -> None:
    assert retry_delay(attempt, jitter=0.0) == timedelta(minutes=minutes)


def test_full_jitter_adds_a_fifth_of_the_base_delay() -> None:
    assert retry_delay(2, jitter=1.0) == timedelta(minutes=6)


@pytest.mark.parametrize(("attempt", "jitter"), [(0, 0.0), (1, -0.1), (1, 1.1)])
def test_invalid_arguments_are_rejected(attempt: int, jitter: float) -> None:
    with pytest.raises(ValueError):
        retry_delay(attempt, jitter=jitter)
