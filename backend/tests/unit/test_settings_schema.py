import pytest
from pydantic import ValidationError

from clipforge.ai.registry import missing_secrets, parse_selection
from clipforge.api.v1.settings import DiscoveryPatch, ModelsPatch, SettingsPatch
from clipforge.discovery.regions import ISO_3166_ALPHA2
from clipforge.discovery.service import DiscoverySettings
from clipforge.queue.errors import PermanentError
from clipforge.settings_schema import (
    AdminSettings,
    JobSettings,
    ModelSelection,
    ProviderSnapshot,
    SettingsSchema,
)


def test_defaults_match_the_spec() -> None:
    settings = SettingsSchema()

    assert (settings.min_score, settings.clip_min_s, settings.clip_max_s) == (75, 60, 90)
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


@pytest.mark.parametrize(
    "entry",
    [
        "https://cdn.example.com",
        "cdn.example.com/videos",
        "10.0.0.1",
        "[::1]",
        "::1",
        "localhost",
        "*.com",
        "*example.com",
        "cdn.*.example.com",
        "example.com:443",
        "user@example.com",
        "-bad.example.com",
        "youtube.com",
        "*.youtube.com",
        "m.youtube.com",
        "youtu.be",
        "rr1---sn-abc.googlevideo.com",
        "*.googlevideo.com",
        "",
    ],
)
def test_invalid_permitted_download_domains_are_rejected(entry: str) -> None:
    with pytest.raises(ValidationError, match="permitted_download_domains"):
        SettingsSchema.model_validate({"permitted_download_domains": [entry]})


def test_permitted_download_domains_accept_hosts_and_wildcard_suffixes() -> None:
    settings = SettingsSchema.model_validate(
        {"permitted_download_domains": ["CDN.Example.com", "*.media.example.org"]}
    )

    assert settings.permitted_download_domains == ["cdn.example.com", "*.media.example.org"]


@pytest.mark.parametrize("region", ["ZZ", "XX", "id", "IDN", "U1"])
def test_discovery_region_must_be_iso_3166_alpha_2(region: str) -> None:
    with pytest.raises(ValidationError, match="region_code"):
        SettingsSchema.model_validate({"discovery": {"region_code": region}})


def test_auto_discovery_defaults_to_daily_at_three() -> None:
    discovery = SettingsSchema().discovery

    assert (discovery.auto_enabled, discovery.auto_hour) == (True, 3)


@pytest.mark.parametrize("hour", [-1, 24])
def test_auto_discovery_hour_must_be_a_clock_hour(hour: int) -> None:
    with pytest.raises(ValidationError, match="auto_hour"):
        SettingsSchema.model_validate({"discovery": {"auto_hour": hour}})


def test_iso_region_list_is_complete() -> None:
    assert len(ISO_3166_ALPHA2) == 249
    assert {"ID", "US", "GB", "SS", "XK"} & ISO_3166_ALPHA2 == {"ID", "US", "GB", "SS"}


@pytest.mark.parametrize(
    "values",
    [
        {"clip_min_s": 19},
        {"clip_max_s": 91},
        {"clip_min_s": 40, "clip_max_s": 30},
        {"max_clips": 0},
        {"max_clips": 4},
        {"min_score": -1},
        {"min_score": 100.5},
        {"max_overlap": 0.21},
        {"max_overlap": -0.01},
    ],
)
def test_admin_settings_use_the_spec_bounds(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AdminSettings.model_validate(values)


def test_admin_settings_accept_the_spec_edges() -> None:
    settings = AdminSettings.model_validate(
        {"clip_min_s": 20, "clip_max_s": 90, "max_overlap": 0.2, "min_score": 100}
    )

    assert (settings.clip_min_s, settings.clip_max_s, settings.max_overlap) == (20, 90, 0.2)


def test_settings_patch_covers_every_admin_setting() -> None:
    assert set(SettingsPatch.model_fields) == {*AdminSettings.model_fields, "providers"}
    assert set(DiscoveryPatch.model_fields) == set(DiscoverySettings.model_fields)
    assert set(ModelsPatch.model_fields) == set(ModelSelection.model_fields)
