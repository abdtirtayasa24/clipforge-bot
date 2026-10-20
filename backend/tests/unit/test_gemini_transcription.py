import base64
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from clipforge.ai.adapters import gemini_client
from clipforge.ai.adapters.gemini_client import BASE_URL, GeminiClient
from clipforge.ai.adapters.gemini_transcription import (
    GeminiAudioSegmentsProvider,
    GeminiTranscriptionProvider,
)
from clipforge.ai.errors import ModelUnavailable
from clipforge.ai.quota import QuotaGuard
from clipforge.ai.registry import ProviderConfigError
from clipforge.ai.types import TranscriptResult
from clipforge.ai.usage import ProviderCall
from clipforge.queue.errors import PermanentError, QuotaDeferred, TransientError
from clipforge.settings_schema import ProviderSnapshot
from tests.clock import FakeClock

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gemini"
API_KEY = "test-gemini-key"
NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
TRANSCRIBE_URL = f"{BASE_URL}/v1beta/models/gemini-3.5-transcribe:generateContent"
FLASH_LITE_URL = f"{BASE_URL}/v1beta/models/gemini-3.5-flash-lite:generateContent"


def fixture(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return loaded


class Recorder:
    def __init__(self) -> None:
        self.calls: list[ProviderCall] = []

    async def __call__(self, call: ProviderCall) -> None:
        self.calls.append(call)


class Sleeps:
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.clock.advance(timedelta(seconds=seconds))


def provider(model: str = "gemini-3.5-transcribe", **overrides: object) -> ProviderSnapshot:
    values: dict[str, object] = {
        "provider": "gemini",
        "model": model,
        "capability": "transcription",
        "secret_ref": "GEMINI_API_KEY",
        "quota_reset": "pacific_midnight",
    }
    return ProviderSnapshot.model_validate(values | overrides)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


@pytest.fixture
def sleeps(clock: FakeClock) -> Sleeps:
    return Sleeps(clock)


@pytest.fixture
def http() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        yield router


async def no_sessions() -> None:
    raise AssertionError("no database access expected")


def client(
    clock: FakeClock, sleeps: Sleeps, snapshot: ProviderSnapshot | None = None
) -> GeminiClient:
    quota = QuotaGuard(no_sessions, clock=clock, sleep=sleeps)  # type: ignore[arg-type]
    return GeminiClient(
        httpx.AsyncClient(), API_KEY, snapshot or provider(), quota=quota, sleep=sleeps
    )


@pytest.fixture
def audio(tmp_path: Path) -> Path:
    path = tmp_path / "chunk_000.mp3"
    path.write_bytes(b"ID3fake-mp3-bytes")
    return path


async def transcribe(
    gemini: GeminiClient, audio: Path, record: Recorder, hint: str | None = "id-ID"
) -> TranscriptResult:
    return await GeminiTranscriptionProvider(gemini).transcribe(
        audio, duration_s=1202, language_hint=hint, record=record
    )


async def test_words_become_sentence_segments(
    http: respx.MockRouter, clock: FakeClock, sleeps: Sleeps, audio: Path
) -> None:
    route = http.post(TRANSCRIBE_URL).respond(200, json=fixture("transcribe_words.json"))
    record = Recorder()

    result = await transcribe(client(clock, sleeps), audio, record)

    assert result.language == "id-ID"
    assert [s.text for s in result.segments] == ["Halo semuanya.", "Hari ini kita bahas AI."]
    assert result.words[0].start_s == pytest.approx(0.12)
    assert result.words[-1].end_s == pytest.approx(3.5)
    request = route.calls.last.request
    assert request.headers["x-goog-api-key"] == API_KEY
    assert API_KEY not in str(request.url)
    body = json.loads(request.content)
    assert body["generationConfig"]["audioTranscriptionConfig"] == {
        "wordTimestamp": True,
        "diarization": False,
        "languageCodes": ["id-ID"],
    }
    inline = body["contents"][0]["parts"][0]["inlineData"]
    assert inline["mimeType"] == "audio/mp3"
    assert base64.b64decode(inline["data"]) == audio.read_bytes()
    assert record.calls == [
        ProviderCall(
            success=True,
            latency_ms=record.calls[0].latency_ms,
            input_tokens=38400,
            output_tokens=2100,
            audio_seconds=1202,
        )
    ]


async def test_without_a_hint_the_language_is_auto_detected(
    http: respx.MockRouter, clock: FakeClock, sleeps: Sleeps, audio: Path
) -> None:
    route = http.post(TRANSCRIBE_URL).respond(200, json=fixture("transcribe_words.json"))

    await transcribe(client(clock, sleeps), audio, Recorder(), hint=None)

    config = json.loads(route.calls.last.request.content)["generationConfig"]
    assert "languageCodes" not in config["audioTranscriptionConfig"]


async def test_server_errors_are_retried_and_every_attempt_recorded(
    http: respx.MockRouter, clock: FakeClock, sleeps: Sleeps, audio: Path
) -> None:
    http.post(TRANSCRIBE_URL).mock(
        side_effect=[
            httpx.Response(503, json={"error": {"code": 503, "status": "UNAVAILABLE"}}),
            httpx.Response(500),
            httpx.Response(200, json=fixture("transcribe_words.json")),
        ]
    )
    record = Recorder()

    result = await transcribe(client(clock, sleeps), audio, record)

    assert result.segments
    assert [(c.success, c.error_code) for c in record.calls] == [
        (False, "503"),
        (False, "500"),
        (True, None),
    ]
    assert sleeps.waits == [2.0, 8.0]


async def test_persistent_server_errors_mean_the_model_is_unavailable(
    http: respx.MockRouter, clock: FakeClock, sleeps: Sleeps, audio: Path
) -> None:
    http.post(TRANSCRIBE_URL).respond(503)
    record = Recorder()

    with pytest.raises(ModelUnavailable):
        await transcribe(client(clock, sleeps), audio, record)

    assert len(record.calls) == gemini_client.MAX_ATTEMPTS


async def test_network_errors_are_retried(
    http: respx.MockRouter, clock: FakeClock, sleeps: Sleeps, audio: Path
) -> None:
    http.post(TRANSCRIBE_URL).mock(
        side_effect=[
            httpx.ConnectTimeout("timed out"),
            httpx.Response(200, json=fixture("transcribe_words.json")),
        ]
    )
    record = Recorder()

    await transcribe(client(clock, sleeps), audio, record)

    assert [(c.success, c.error_code) for c in record.calls] == [
        (False, "ConnectTimeout"),
        (True, None),
    ]


async def test_short_rate_limit_waits_for_retry_after(
    http: respx.MockRouter, clock: FakeClock, sleeps: Sleeps, audio: Path
) -> None:
    http.post(TRANSCRIBE_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, json=fixture("transcribe_words.json")),
        ]
    )

    await transcribe(client(clock, sleeps), audio, Recorder())

    assert sleeps.waits == [7.0]


async def test_rate_limit_delay_from_the_error_body_is_honored(
    http: respx.MockRouter, clock: FakeClock, sleeps: Sleeps, audio: Path
) -> None:
    retry_info = {
        "@type": "type.googleapis.com/google.rpc.RetryInfo",
        "retryDelay": "33s",
    }
    body = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": [retry_info]}}
    http.post(TRANSCRIBE_URL).mock(
        side_effect=[
            httpx.Response(429, json=body),
            httpx.Response(200, json=fixture("transcribe_words.json")),
        ]
    )

    await transcribe(client(clock, sleeps), audio, Recorder())

    assert sleeps.waits == [33.0]


async def test_long_rate_limit_defers_the_job(
    http: respx.MockRouter, clock: FakeClock, sleeps: Sleeps, audio: Path
) -> None:
    http.post(TRANSCRIBE_URL).respond(429, headers={"Retry-After": "3600"})
    record = Recorder()

    with pytest.raises(QuotaDeferred) as deferred:
        await transcribe(client(clock, sleeps), audio, record)

    assert deferred.value.until == NOW + timedelta(hours=1)
    assert sleeps.waits == []
    assert [c.error_code for c in record.calls] == ["429"]


async def test_rate_limits_that_persist_defer_the_job(
    http: respx.MockRouter, clock: FakeClock, sleeps: Sleeps, audio: Path
) -> None:
    http.post(TRANSCRIBE_URL).respond(429, headers={"Retry-After": "10"})

    with pytest.raises(QuotaDeferred):
        await transcribe(client(clock, sleeps), audio, Recorder())

    assert sleeps.waits == [10.0, 10.0]


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (404, ModelUnavailable),
        (403, ProviderConfigError),
        (401, ProviderConfigError),
        (400, PermanentError),
    ],
)
async def test_client_errors_are_not_retried(
    http: respx.MockRouter,
    clock: FakeClock,
    sleeps: Sleeps,
    audio: Path,
    status: int,
    error: type[Exception],
) -> None:
    http.post(TRANSCRIBE_URL).respond(status, json={"error": {"status": "X"}})
    record = Recorder()

    with pytest.raises(error) as raised:
        await transcribe(client(clock, sleeps), audio, record)

    assert API_KEY not in str(raised.value)
    assert len(record.calls) == 1


async def test_rpm_limit_spaces_out_requests(
    http: respx.MockRouter, clock: FakeClock, sleeps: Sleeps, audio: Path
) -> None:
    http.post(TRANSCRIBE_URL).respond(200, json=fixture("transcribe_words.json"))
    gemini = client(clock, sleeps, provider(rpm_limit=1))

    await transcribe(gemini, audio, Recorder())
    await transcribe(gemini, audio, Recorder())

    assert sleeps.waits == [pytest.approx(60.0)]


async def test_large_audio_goes_through_the_files_api(
    http: respx.MockRouter,
    clock: FakeClock,
    sleeps: Sleeps,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gemini_client, "MAX_INLINE_BYTES", 4)
    big = tmp_path / "big.mp3"
    big.write_bytes(b"0123456789")
    upload_url = "https://upload.example.test/session/1"
    start = http.post(f"{BASE_URL}/upload/v1beta/files").respond(
        200, headers={"X-Goog-Upload-URL": upload_url}
    )
    file = {"name": "files/abc", "uri": f"{BASE_URL}/v1beta/files/abc", "state": "PROCESSING"}
    finish = http.post(upload_url).respond(200, json={"file": file})
    http.get(f"{BASE_URL}/v1beta/files/abc").respond(200, json=file | {"state": "ACTIVE"})
    generate = http.post(TRANSCRIBE_URL).respond(200, json=fixture("transcribe_words.json"))

    await transcribe(client(clock, sleeps), big, Recorder())

    assert start.calls.last.request.headers["X-Goog-Upload-Header-Content-Length"] == "10"
    assert finish.calls.last.request.content == b"0123456789"
    part = json.loads(generate.calls.last.request.content)["contents"][0]["parts"][0]
    assert part == {"fileData": {"mimeType": "audio/mp3", "fileUri": file["uri"]}}


async def test_fallback_returns_clamped_sorted_segments(
    http: respx.MockRouter, clock: FakeClock, sleeps: Sleeps, audio: Path
) -> None:
    route = http.post(FLASH_LITE_URL).respond(200, json=fixture("fallback_segments.json"))
    record = Recorder()
    gemini = client(clock, sleeps, provider("gemini-3.5-flash-lite"))

    result = await GeminiAudioSegmentsProvider(gemini).transcribe(
        audio, duration_s=602, language_hint=None, record=record
    )

    assert result.language == "id"
    assert [(s.text, s.start_s, s.end_s) for s in result.segments] == [
        ("Kalimat pertama.", 0.0, 3.2),
        ("Kalimat kedua.", 4.0, 7.5),
        ("Melewati akhir.", 598.0, 602.0),
    ]
    assert result.words == []
    config = json.loads(route.calls.last.request.content)["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert record.calls[0].audio_seconds == 602
    assert record.calls[0].input_tokens == 19500


async def test_malformed_fallback_output_is_transient(
    http: respx.MockRouter, clock: FakeClock, sleeps: Sleeps, audio: Path
) -> None:
    body = {"candidates": [{"content": {"parts": [{"text": "not json"}]}}]}
    http.post(FLASH_LITE_URL).respond(200, json=body)
    gemini = client(clock, sleeps, provider("gemini-3.5-flash-lite"))

    with pytest.raises(TransientError, match="malformed"):
        await GeminiAudioSegmentsProvider(gemini).transcribe(
            audio, duration_s=602, language_hint=None, record=Recorder()
        )


@pytest.mark.live
async def test_live_transcription_of_a_generated_tone(tmp_path: Path) -> None:
    """Opt-in: `GEMINI_API_KEY=... python -m pytest -m live -k transcri`."""
    import os
    import subprocess

    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        pytest.skip("GEMINI_API_KEY is not set")
    path = tmp_path / "tone.mp3"
    subprocess.run(  # noqa: S603, ASYNC221 - fixed arguments, opt-in test
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=duration=5", str(path)],  # noqa: S607
        check=True,
    )
    clock = FakeClock(datetime.now(UTC))
    quota = QuotaGuard(no_sessions, clock=clock)  # type: ignore[arg-type]
    async with httpx.AsyncClient() as http:
        gemini = GeminiClient(http, key, provider(), quota=quota)
        result = await GeminiTranscriptionProvider(gemini).transcribe(
            path, duration_s=5, language_hint=None, record=Recorder()
        )
    assert all(0 <= w.start_s <= w.end_s <= 6 for w in result.words)


async def test_word_times_that_go_backwards_are_made_monotonic(
    http: respx.MockRouter, clock: FakeClock, sleeps: Sleeps, audio: Path
) -> None:
    timed = [("Bene", "470.2s", "470.5s"), ("mau", "470.6s", "470.9s"), ("Oke.", "468.8s", "469s")]
    words = [{"word": w, "startOffset": s, "endOffset": e} for w, s, e in timed]
    body = {"candidates": [{"content": {"parts": [{"audioTranscription": {"words": words}}]}}]}
    http.post(TRANSCRIBE_URL).respond(200, json=body)

    result = await transcribe(client(clock, sleeps), audio, Recorder())

    assert [s.text for s in result.segments] == ["Bene mau Oke."]
    starts = [word.start_s for word in result.words]
    assert starts == sorted(starts)
    assert all(word.end_s >= word.start_s for word in result.words)
    assert result.segments[0].end_s >= result.segments[0].start_s
