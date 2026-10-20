from decimal import Decimal

import pytest

from clipforge.ai.usage import ProviderCall, estimate_cost
from clipforge.settings_schema import PriceSnapshot

TOKEN_PRICED = PriceSnapshot(input_per_mtok=Decimal("0.10"), output_per_mtok=Decimal("0.40"))
AUDIO_PRICED = PriceSnapshot(per_audio_minute=Decimal("0.006"))


@pytest.mark.parametrize(
    ("call", "price", "expected"),
    [
        (ProviderCall(True, 10, input_tokens=12_000, output_tokens=800), TOKEN_PRICED, "0.00152"),
        (ProviderCall(True, 10, input_tokens=1_000_000), TOKEN_PRICED, "0.1"),
        (ProviderCall(True, 10, audio_seconds=600), AUDIO_PRICED, "0.06"),
        (ProviderCall(True, 10, audio_seconds=90.5), AUDIO_PRICED, "0.00905"),
        (ProviderCall(True, 10, input_tokens=1), TOKEN_PRICED, "0.0000001"),
        (ProviderCall(False, 10, error_code="429"), TOKEN_PRICED, "0"),
        (ProviderCall(True, 10, input_tokens=50_000, audio_seconds=60), PriceSnapshot(), "0"),
    ],
)
def test_estimated_cost(call: ProviderCall, price: PriceSnapshot, expected: str) -> None:
    assert estimate_cost(call, price) == Decimal(expected)


def test_cost_is_rounded_to_the_stored_precision() -> None:
    cost = estimate_cost(ProviderCall(True, 1, input_tokens=1), PriceSnapshot(input_per_mtok=1))

    assert cost == Decimal("0.00000100")
    assert cost.as_tuple().exponent == -8
