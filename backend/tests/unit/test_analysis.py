import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from clipforge.ai.adapters.fake import FakeAnalysisProvider, spoken_transcript
from clipforge.ai.adapters.gemini_analysis import GeminiAnalysisProvider
from clipforge.ai.adapters.gemini_client import BASE_URL, GeminiClient
from clipforge.ai.adapters.openrouter_analysis import (
    CHAT_URL,
    OpenRouterAnalysisProvider,
    OpenRouterClient,
)
from clipforge.ai.prompts.analysis import TITLE_MAX_CHARS, truncate_title
from clipforge.ai.quota import QuotaGuard
from clipforge.ai.types import (
    AnalysisContext,
    ClipCandidate,
    DimensionScores,
    Frame,
    Segment,
    TranscriptResult,
)
from clipforge.ai.usage import ProviderCall
from clipforge.ai.windowing import Analyzer, ProviderChoice, clamp, frames_for, windows
from clipforge.queue.errors import QuotaDeferred, TransientError
from clipforge.settings_schema import ProviderSnapshot, QuotaReset
from tests.clock import FakeClock

NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
FLASH_LITE_URL = f"{BASE_URL}/v1beta/models/gemini-3.5-flash-lite:generateContent"
CONTEXT = AnalysisContext(
    video_title="Ngobrol santai soal AI", channel_title="Kanal Uji", language="id", duration_s=1200
)
SEGMENTS = [
    Segment(text=f"Kalimat nomor {i}.", start_s=i * 5.0, end_s=i * 5.0 + 5) for i in range(240)
]
TRANSCRIPT = TranscriptResult(language="id", segments=SEGMENTS)


def candidate_json(start: float, end: float, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "start_s": start,
        "end_s": end,
        "hook": 90,
        "emotion": 80,
        "clarity": 85,
        "relevance": 75,
        "visual": 60,
        "completeness": 80,
        "title": "Rahasia AI yang jarang dibahas",
        "hook_line": "Ternyata AI bisa begini",
        "topic": "teknologi",
        "keywords": ["ai", "teknologi"],
        "explanation": "Pembuka kuat dan tuntas.",
    }
    return values | overrides


def gemini_body(payload: object, *, raw: str | None = None) -> dict[str, Any]:
    text = raw if raw is not None else json.dumps(payload)
    return {
        "candidates": [{"content": {"parts": [{"text": text}], "role": "model"}}],
        "usageMetadata": {"promptTokenCount": 5200, "candidatesTokenCount": 640},
    }


def openrouter_body(content: str) -> dict[str, Any]:
    return {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 5100, "completion_tokens": 700},
    }


class Recorder:
    def __init__(self) -> None:
        self.calls: list[ProviderCall] = []

    async def __call__(self, call: ProviderCall) -> None:
        self.calls.append(call)


async def no_sleep(_: float) -> None:
    return None


class StubQuota(QuotaGuard):
    """Daily usage from a dict instead of the database."""

    def __init__(self, used: dict[str, int] | None = None) -> None:
        super().__init__(None, clock=lambda: NOW, sleep=no_sleep)  # type: ignore[arg-type]
        self.used = used or {}

    async def used_today(self, provider: str, model: str, reset: QuotaReset) -> int:
        return self.used.get(model, 0)


def snapshot(provider: str, model: str, **overrides: object) -> ProviderSnapshot:
    values: dict[str, object] = {
        "provider": provider,
        "model": model,
        "capability": "analysis",
        "secret_ref": "KEY",
    }
    return ProviderSnapshot.model_validate(values | overrides)


GEMINI = snapshot("gemini", "gemini-3.5-flash-lite", quota_reset="pacific_midnight")
OPENROUTER = snapshot("openrouter", "nvidia/nemotron-3.5-super-vl:free")


@pytest.fixture
def http() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        yield router


def gemini_provider(quota: QuotaGuard, model: ProviderSnapshot = GEMINI) -> GeminiAnalysisProvider:
    client = GeminiClient(httpx.AsyncClient(), "gemini-key", model, quota=quota, sleep=no_sleep)
    return GeminiAnalysisProvider(client)


def openrouter_provider(quota: QuotaGuard) -> OpenRouterAnalysisProvider:
    client = OpenRouterClient(
        httpx.AsyncClient(), "openrouter-key", OPENROUTER, quota=quota, sleep=no_sleep
    )
    return OpenRouterAnalysisProvider(client)


async def test_gemini_window_pass_returns_valid_candidates(http: respx.MockRouter) -> None:
    route = http.post(FLASH_LITE_URL).respond(
        200, json=gemini_body({"candidates": [candidate_json(100, 135)]})
    )
    record = Recorder()

    found = await gemini_provider(StubQuota()).propose(SEGMENTS[:108], CONTEXT, record=record)

    assert len(found) == 1
    assert (found[0].start_s, found[0].end_s, found[0].scores.hook) == (100, 135, 90)
    assert found[0].title == "Rahasia AI yang jarang dibahas"
    assert [(c.success, c.input_tokens, c.output_tokens) for c in record.calls] == [
        (True, 5200, 640)
    ]
    request = json.loads(route.calls.last.request.content)
    assert request["generationConfig"]["responseMimeType"] == "application/json"
    assert "candidates" in request["generationConfig"]["responseSchema"]["properties"]
    prompt = request["contents"][0]["parts"][0]["text"]
    assert "[100.0-105.0] Kalimat nomor 20." in prompt
    assert "Ngobrol santai soal AI" in prompt
    assert "Transcript language: id" in prompt
    assert "never invent facts" in request["systemInstruction"]["parts"][0]["text"]


async def test_malformed_output_gets_one_repair_turn(http: respx.MockRouter) -> None:
    route = http.post(FLASH_LITE_URL).mock(
        side_effect=[
            httpx.Response(200, json=gemini_body(None, raw='{"candidates": [{"start_s": 1}]}')),
            httpx.Response(200, json=gemini_body({"candidates": [candidate_json(10, 40)]})),
        ]
    )
    record = Recorder()

    found = await gemini_provider(StubQuota()).propose(SEGMENTS[:20], CONTEXT, record=record)

    assert [c.start_s for c in found] == [10]
    assert len(record.calls) == 2
    repair = json.loads(route.calls.last.request.content)["contents"]
    assert [turn["role"] for turn in repair] == ["user", "model", "user"]
    assert "not valid" in repair[2]["parts"][0]["text"]


async def test_malformed_output_twice_is_transient(http: respx.MockRouter) -> None:
    http.post(FLASH_LITE_URL).respond(200, json=gemini_body(None, raw="not json"))

    with pytest.raises(TransientError, match="invalid JSON"):
        await gemini_provider(StubQuota()).propose(SEGMENTS[:20], CONTEXT, record=Recorder())


async def test_blank_title_needs_a_repair(http: respx.MockRouter) -> None:
    http.post(FLASH_LITE_URL).mock(
        side_effect=[
            httpx.Response(
                200, json=gemini_body({"candidates": [candidate_json(10, 40, title=" ")]})
            ),
            httpx.Response(200, json=gemini_body({"candidates": [candidate_json(10, 40)]})),
        ]
    )

    found = await gemini_provider(StubQuota()).propose(SEGMENTS[:20], CONTEXT, record=Recorder())

    assert found[0].title and found[0].hook_line


async def test_scores_out_of_range_are_clamped(http: respx.MockRouter) -> None:
    payload = {"candidates": [candidate_json(10, 40, hook=130, emotion=-5, visual=72.6)]}
    http.post(FLASH_LITE_URL).respond(200, json=gemini_body(payload))

    found = await gemini_provider(StubQuota()).propose(SEGMENTS[:20], CONTEXT, record=Recorder())

    assert (found[0].scores.hook, found[0].scores.emotion, found[0].scores.visual) == (100, 0, 73)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Pendek", "Pendek"),
        ("  spasi   berlebih  ", "spasi berlebih"),
        (
            "Kenapa banyak orang Indonesia mulai memakai kecerdasan buatan untuk bekerja "
            "setiap hari",
            "Kenapa banyak orang Indonesia mulai memakai kecerdasan buatan untuk",
        ),
        ("x" * 90, "x" * 70),
    ],
)
def test_long_titles_are_cut_at_a_word_boundary(title: str, expected: str) -> None:
    assert truncate_title(title) == expected
    assert len(truncate_title(title)) <= TITLE_MAX_CHARS


async def test_generated_titles_are_truncated(http: respx.MockRouter) -> None:
    long_title = "Kenapa banyak orang Indonesia mulai memakai kecerdasan buatan untuk bekerja"
    payload = {"candidates": [candidate_json(10, 40, title=long_title)]}
    http.post(FLASH_LITE_URL).respond(200, json=gemini_body(payload))

    found = await gemini_provider(StubQuota()).propose(SEGMENTS[:20], CONTEXT, record=Recorder())

    assert len(found[0].title) <= TITLE_MAX_CHARS
    assert long_title.startswith(found[0].title)


def scored(start: float, end: float, hook: int = 80) -> ClipCandidate:
    return ClipCandidate(
        start_s=start,
        end_s=end,
        scores=DimensionScores(
            hook=hook, emotion=80, clarity=80, relevance=80, visual=80, completeness=80
        ),
        title="t",
        hook_line="h",
    )


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [(-5, 30, (0, 30)), (1190, 1250, (1190, 1200)), (1250, 1290, None), (40, 40, None)],
)
def test_out_of_range_times_are_clamped_or_dropped(
    start: float, end: float, expected: tuple[float, float] | None
) -> None:
    clamped = clamp(scored(0, 1).model_copy(update={"start_s": start, "end_s": end}), 1200)

    assert (clamped.start_s, clamped.end_s) == expected if clamped else expected is None


def test_long_transcripts_become_overlapping_windows() -> None:
    parts = windows(spoken_transcript(7200))

    assert 13 <= len(parts) <= 16
    assert parts[0][0].start_s == 0
    assert parts[-1][-1].end_s == pytest.approx(7200)
    for previous, following in pairwise(parts):
        assert following[0].start_s < previous[-1].end_s


def frame(time_s: float) -> Frame:
    return Frame(time_s=time_s, path=Path(f"frame_{time_s:g}.jpg"))


def test_frames_come_only_from_shortlisted_ranges() -> None:
    frames = [frame(t) for t in range(0, 1200, 5)]

    sent = frames_for([scored(100, 130), scored(600, 640)], frames)

    assert sent
    assert all(100 <= f.time_s <= 130 or 600 <= f.time_s <= 640 for f in sent)
    assert len(sent) <= 8


def analyzer(quota: QuotaGuard, recorders: dict[str, Recorder]) -> Analyzer:
    def recorder_for(provider: ProviderSnapshot) -> Recorder:
        return recorders.setdefault(provider.model, Recorder())

    return Analyzer(quota, recorder_for)


async def test_analyzer_sends_frames_for_the_shortlist_only() -> None:
    proposals = [scored(100, 130, hook=95), scored(400, 430, hook=40), scored(900, 940, hook=90)]
    fake = FakeAnalysisProvider(proposals)
    frames = [frame(t) for t in range(0, 1200, 10)]
    chain = [ProviderChoice(GEMINI, fake)]

    outcome = await analyzer(StubQuota(), {}).analyze(
        TRANSCRIPT, CONTEXT, frames, window_chain=chain, rerank_chain=chain, shortlist_size=2
    )

    assert [(c.start_s, c.end_s) for c in outcome.shortlist] == [(100, 130), (900, 940)]
    assert fake.reranked_frames
    assert all(100 <= f.time_s <= 130 or 900 <= f.time_s <= 940 for f in fake.reranked_frames)
    assert outcome.frames_sent == len(fake.reranked_frames)
    assert len(outcome.window_models) == len(windows(TRANSCRIPT))


async def test_gemini_rate_limit_falls_back_to_openrouter(http: respx.MockRouter) -> None:
    http.post(FLASH_LITE_URL).respond(429, headers={"Retry-After": "7200"})
    proposal = json.dumps({"candidates": [candidate_json(100, 130)]})
    openrouter = http.post(CHAT_URL).respond(200, json=openrouter_body(f"```json\n{proposal}\n```"))
    quota = StubQuota()
    recorders: dict[str, Recorder] = {}
    chain = [
        ProviderChoice(GEMINI, gemini_provider(quota)),
        ProviderChoice(OPENROUTER, openrouter_provider(quota)),
    ]

    proposals, models = await analyzer(quota, recorders).propose_all(TRANSCRIPT, CONTEXT, chain)

    assert models == [OPENROUTER.model] * len(windows(TRANSCRIPT))
    assert proposals and all(c.title for c in proposals)
    assert [c.error_code for c in recorders[GEMINI.model].calls] == ["429"]
    assert all(c.success for c in recorders[OPENROUTER.model].calls)
    assert recorders[OPENROUTER.model].calls[0].input_tokens == 5100
    request = openrouter.calls.last.request
    assert request.headers["Authorization"] == "Bearer openrouter-key"
    body = json.loads(request.content)
    assert body["model"] == OPENROUTER.model
    assert "JSON schema of the answer" in body["messages"][1]["content"][0]["text"]


async def test_exhausted_daily_quota_moves_the_whole_pass_to_the_fallback(
    http: respx.MockRouter,
) -> None:
    proposal = json.dumps({"candidates": []})
    http.post(CHAT_URL).respond(200, json=openrouter_body(proposal))
    gemini_route = http.post(FLASH_LITE_URL)
    quota = StubQuota({GEMINI.model: 200})
    limited_gemini = GEMINI.model_copy(update={"rpd_limit": 200})
    chain = [
        ProviderChoice(limited_gemini, gemini_provider(quota, limited_gemini)),
        ProviderChoice(OPENROUTER, openrouter_provider(quota)),
    ]

    _, models = await analyzer(quota, {}).propose_all(TRANSCRIPT, CONTEXT, chain)

    assert set(models) == {OPENROUTER.model}
    assert not gemini_route.called


async def test_both_providers_out_of_quota_defers_the_job() -> None:
    quota = StubQuota({GEMINI.model: 200, OPENROUTER.model: 50})
    chain = [
        ProviderChoice(GEMINI.model_copy(update={"rpd_limit": 200}), FakeAnalysisProvider([])),
        ProviderChoice(OPENROUTER.model_copy(update={"rpd_limit": 50}), FakeAnalysisProvider([])),
    ]

    with pytest.raises(QuotaDeferred) as deferred:
        await analyzer(quota, {}).propose_all(TRANSCRIPT, CONTEXT, chain)

    # The earliest reset wins: OpenRouter's UTC midnight is before Gemini's Pacific midnight.
    assert deferred.value.until == datetime(2026, 10, 21, tzinfo=UTC)


async def test_rate_limits_on_both_providers_defer_the_job(http: respx.MockRouter) -> None:
    http.post(FLASH_LITE_URL).respond(429, headers={"Retry-After": "7200"})
    http.post(CHAT_URL).respond(429, headers={"Retry-After": "600"})
    quota = StubQuota()
    chain = [
        ProviderChoice(GEMINI, gemini_provider(quota)),
        ProviderChoice(OPENROUTER, openrouter_provider(quota)),
    ]

    with pytest.raises(QuotaDeferred) as deferred:
        await analyzer(quota, {}).propose_all(TRANSCRIPT, CONTEXT, chain)

    assert deferred.value.until == NOW + timedelta(seconds=600)


async def test_rerank_uses_frames_and_applies_scores_by_index(
    http: respx.MockRouter, tmp_path: Path
) -> None:
    rerank_model = GEMINI.model_copy(update={"model": "gemini-3-flash-preview"})
    url = f"{BASE_URL}/v1beta/models/gemini-3-flash-preview:generateContent"
    rescore = {
        "candidates": [
            {"index": 1, "hook": 99, "emotion": 90, "clarity": 90, "relevance": 90, "visual": 95,
             "completeness": 90, "title": "Judul baru", "hook_line": "Kalimat pembuka baru"},
            {"index": 7, "hook": 1, "emotion": 1, "clarity": 1, "relevance": 1, "visual": 1,
             "completeness": 1},
        ]
    }  # fmt: skip
    route = http.post(url).respond(200, json=gemini_body(rescore))
    image = tmp_path / "frame.jpg"
    image.write_bytes(b"\xff\xd8jpeg")
    shortlist = [scored(100, 130), scored(600, 640)]

    rescored = await gemini_provider(StubQuota(), rerank_model).rerank(
        shortlist, TRANSCRIPT, [Frame(time_s=610, path=image)], CONTEXT, record=Recorder()
    )

    assert rescored[0] == shortlist[0]
    assert (rescored[1].scores.visual, rescored[1].title) == (95, "Judul baru")
    assert (rescored[1].start_s, rescored[1].end_s) == (600, 640)
    parts = json.loads(route.calls.last.request.content)["contents"][0]["parts"]
    assert parts[1] == {"text": "Frame at 610.0 s:"}
    assert parts[2]["inlineData"]["mimeType"] == "image/jpeg"
    assert "Candidate 1 [600.0-640.0]" in parts[0]["text"]


async def test_rerank_falls_back_to_flash_lite_when_preview_quota_is_used() -> None:
    preview = GEMINI.model_copy(update={"model": "gemini-3-flash-preview", "rpd_limit": 50})
    quota = StubQuota({preview.model: 50})
    lite = FakeAnalysisProvider([])
    chain = [ProviderChoice(preview, FakeAnalysisProvider([])), ProviderChoice(GEMINI, lite)]

    _, model = await analyzer(quota, {}).rerank([scored(1, 30)], TRANSCRIPT, [], CONTEXT, chain)

    assert model == GEMINI.model


@pytest.mark.live
async def test_live_gemini_analysis_window() -> None:
    """Opt-in: `GEMINI_API_KEY=... python -m pytest -m live -k analysis`."""
    import os

    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        pytest.skip("GEMINI_API_KEY is not set")
    clock = FakeClock(datetime.now(UTC))
    quota = QuotaGuard(None, clock=clock)  # type: ignore[arg-type]
    async with httpx.AsyncClient() as http:
        provider = GeminiAnalysisProvider(GeminiClient(http, key, GEMINI, quota=quota))
        found = await provider.propose(SEGMENTS[:60], CONTEXT, record=Recorder())
    assert all(0 <= c.start_s < c.end_s for c in found)
