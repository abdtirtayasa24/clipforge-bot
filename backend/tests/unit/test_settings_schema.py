import pytest
from pydantic import ValidationError

from clipforge.ai.registry import missing_secrets, parse_selection
from clipforge.queue.errors import PermanentError
from clipforge.settings_schema import JobSettings, ProviderSnapshot, SettingsSchema


def test_defaults_match_the_spec() -> None:
    settings = SettingsSchema()

    assert (settings.min_score, settings.clip_min_s, settings.clip_max_s) == (75, 20, 60)
    assert (settings.max_clips, settings.max_overlap, settings.audio_chunk_minutes) == (3, 0.2, 20)
    assert settings.models.transcription == "gemini-3.5-transcribe"


@pytest.mark.parametrize(
    "values",
    [
        {"min_score": 101},
        {"max_clips": 4},
        {"max_overlap": -0.1},
        {"audio_chunk_minutes": 29},
        {"clip_min_s": 60, "clip_max_s": 60},
    ],
)
def test_out_of_bounds_values_are_rejected(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        SettingsSchema.model_validate(values)


def test_unknown_keys_are_ignored() -> None:
    assert SettingsSchema.model_validate({"youtube_categories": {"1": "Film"}}).min_score == 75


def test_empty_snapshot_has_defaults_and_no_providers() -> None:
    settings = JobSettings.of({})

    assert settings.min_score == 75
    with pytest.raises(PermanentError, match="transcription"):
        settings.provider("transcription")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("gemini-3.5-flash-lite", (None, "gemini-3.5-flash-lite")),
        (
            "openrouter:nvidia/nemotron-3.5-super-vl:free",
            ("openrouter", "nvidia/nemotron-3.5-super-vl:free"),
        ),
        ("nvidia/nemotron-3.5-super-vl:free", (None, "nvidia/nemotron-3.5-super-vl:free")),
    ],
)
def test_model_selection_parsing(value: str, expected: tuple[str | None, str]) -> None:
    assert parse_selection(value) == expected


def test_missing_secrets_lists_names_only() -> None:
    def snapshot(secret_ref: str) -> ProviderSnapshot:
        return ProviderSnapshot(
            provider="gemini", model="m", capability="analysis", secret_ref=secret_ref
        )

    providers = {
        "transcription": snapshot("GEMINI_API_KEY"),
        "analysis": snapshot("GEMINI_API_KEY"),
        "analysis_fallback": snapshot("OPENROUTER_API_KEY"),
    }

    assert missing_secrets(providers, {"GEMINI_API_KEY": "k"}) == ["OPENROUTER_API_KEY"]  # type: ignore[arg-type]
    assert missing_secrets(providers, {"GEMINI_API_KEY": "k", "OPENROUTER_API_KEY": "k"}) == []  # type: ignore[arg-type]
