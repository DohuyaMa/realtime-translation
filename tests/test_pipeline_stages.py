"""Staged pipeline (owner 2026-10-31): the LLM translates unit n+1 while TTS
synthesizes n and n-1 plays, order kept, LLM sentences go to TTS/playback as
they come. Units come from rt-asr stream finals (``FakeStream``)."""

import threading
import time

import httpx
import pytest
import numpy as np

from src.clients.asr import AsrResult
from src.clients.llm import LlmClient, _pieces
from src.core.runtime import LlmSettings
from test_translation_system import (
    FakeAsr,
    FakeCapture,
    FakePools,
    FakeStreams,
    FakeTts,
    StaticTarget,
    final,
    translate_settings,
    wait_for,
)
from src.clients import Directory
from src.translation_system import TTS_WARMUP_TEXT, TranslationSystem

STAGE_S = 0.1


class SlowAsr(FakeAsr):
    """What rt-asr will commit: each ``speak`` takes ``delay`` (commit lag)
    and emits the next result as one final with an endpoint."""

    def __init__(self, results, delay, clock=None):
        super().__init__()
        self.results = list(results)
        self.delay, self.clock = delay, clock
        self.t = 0.0  # stream seconds spoken so far

    def speak(self, streams):
        r = self.results.pop(0)
        if self.clock is not None:
            self.clock.t += self.delay
        else:
            time.sleep(self.delay)
        msg = final(r.text, self.t, language=r.language)
        self.t = msg["end"] + 0.5
        streams["asr"].emit(msg)


class SlowLlm:
    def __init__(self, delay, clock=None, sentences=None, gate=None):
        self.delay, self.clock = delay, clock
        self.sentences = sentences
        self.gate = gate
        self.calls = []

    def translate_stream(self, text, source_lang, context=(), check=None, next_text=""):
        self.calls.append(text)
        if self.clock is not None:
            self.clock.t += self.delay
        else:
            time.sleep(self.delay)
        for i, s in enumerate(self.sentences or [f"EN({text})"]):
            if i and self.gate is not None:  # later sentences wait for playback
                assert self.gate.wait(3)
            yield s

    def warm_up(self):
        pass

    base_url = "http://llm"

    def close(self):
        pass


class SlowTts(FakeTts):
    def __init__(self, delay, clock=None):
        super().__init__()
        self.delay, self.clock = delay, clock

    def synthesize(self, text, voice=None, speed=None):
        if text == TTS_WARMUP_TEXT:
            return super().synthesize(text, voice, speed)
        if self.clock is not None:
            self.clock.t += self.delay
        else:
            time.sleep(self.delay)
        self.calls.append(text)
        return np.full(10, len(self.calls), dtype=np.float32), 24000


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def system(asr, llm, tts, sink, clock=time.monotonic):
    """(ts, speak, capture): ``speak(n)`` = the next n results of ``asr``."""
    capture = FakeCapture()
    streams = FakeStreams()
    ts = TranslationSystem(
        translate_settings(),
        directory_factory=lambda s: Directory(),
        pools_factory=lambda s, d: FakePools(asr, tts),
        llm_target_factory=lambda s, d: StaticTarget("http://llm"),
        llm_factory=lambda t, s: llm,
        capture_factory=capture,
        playback_sink=sink,
        clock=clock,
        stream_factory=streams,
    )

    def speak(n=1):
        for _ in range(n):
            asr.speak(streams)

    return ts, speak, capture


def run_three(asr_delay=STAGE_S, llm_delay=STAGE_S, tts_delay=STAGE_S):
    played = []
    asr = SlowAsr([AsrResult(f"p{i}.", "pl") for i in (1, 2, 3)], asr_delay)
    llm = SlowLlm(llm_delay)
    tts = SlowTts(tts_delay)
    ts, speak, capture = system(asr, llm, tts, lambda a, sr: played.append(a[0]))
    assert ts.start()
    try:
        t0 = time.monotonic()
        speak(3)
        assert wait_for(lambda: len(played) == 3, timeout=5)
        elapsed = time.monotonic() - t0
    finally:
        ts.stop()
    return elapsed, played, tts.calls


def test_stages_overlap_and_order_is_kept():
    elapsed, played, tts_calls = run_three()
    sequential = 3 * 3 * STAGE_S  # old model: asr+llm+tts per phrase, one at a time
    # pipelined: first phrase 3 stages, then one stage per further phrase
    assert elapsed < 0.75 * sequential, (elapsed, sequential)
    assert tts_calls == ["EN(p1.)", "EN(p2.)", "EN(p3.)"]
    assert played == [1.0, 2.0, 3.0]


def test_playback_starts_with_first_sentence_before_llm_finishes():
    played = []
    gate = threading.Event()

    def sink(audio, sr):
        played.append(audio[0])
        gate.set()  # the LLM yields its 2nd sentence only after this

    # > SHORT_SOURCE_WORDS: short sources are buffered whole, not streamed
    asr = SlowAsr([AsrResult("raz dwa trzy cztery", "pl")], 0.0)
    llm = SlowLlm(0.0, sentences=["One.", "Two."], gate=gate)
    tts = SlowTts(0.0)
    ts, speak, capture = system(asr, llm, tts, sink)
    assert ts.start()
    try:
        speak()
        assert wait_for(lambda: len(played) == 2)
    finally:
        ts.stop()
    assert tts.calls == ["One.", "Two."]  # TTS per sentence


def test_stage_timing_with_fake_clock():
    clock = Clock()
    played = []
    # commit lag 1.2 s, cyborg LLM 0.4 s and TTS 4.7 s (measured 2026-10-31)
    asr = SlowAsr([AsrResult("raz.", "pl")], 1.2, clock)
    llm = SlowLlm(0.4, clock)
    tts = SlowTts(4.7, clock)
    ts, speak, capture = system(asr, llm, tts, lambda a, sr: played.append(1), clock)
    assert ts.start()
    try:
        for _ in range(30):  # 30 x 1024 samples at 48 kHz = 0.64 s fed at t=100
            capture.push()
        assert wait_for(lambda: ts._fed_samples >= 30 * 1024 // 3)
        speak()  # "raz." ends at 0.3 s of the stream; committed 1.2 s later
        assert wait_for(lambda: played)
        assert wait_for(lambda: ts.get_stats()["timing"])
        t = ts.get_stats()["timing"]
    finally:
        ts.stop()
    assert (t["commit_ms"], t["llm_ms"], t["tts_ms"]) == (1200.0, 400.0, 4700.0)
    assert t["e2e_ms"] == 6300.0 and t["seq"] == 1
    lat = ts.get_stats()["latency"]
    assert lat["e2e_ms"] == {"p50": 6300.0, "p90": 6300.0, "n": 1}


def test_full_stage_queue_is_backpressure_not_drop():
    from src import translation_system as tsmod

    played = []
    n = tsmod.STAGE_QUEUE_MAX + 3
    asr = SlowAsr([AsrResult(f"p{i}.", "pl") for i in range(n)], 0.0)
    llm = SlowLlm(0.0)
    tts = SlowTts(0.05)  # slowest stage
    events = []
    ts, speak, capture = system(asr, llm, tts, lambda a, sr: played.append(a[0]))
    ts.set_status_callback(events.append)
    assert ts.start()
    try:
        speak(n)
        assert wait_for(lambda: len(played) == n, timeout=5)
    finally:
        ts.stop()
    stats = ts.get_stats()
    assert played == [float(i) for i in range(1, n + 1)]
    assert stats["backpressure"]["tts"] >= 1
    assert {"status": "backpressure", "stage": "tts"} in events


def test_pieces_from_stream_deltas():
    deltas = ["Hello wor", "ld. How", " are you? Fi", "ne"]
    assert list(_pieces(deltas)) == ["Hello world.", "How are you?", "Fine"]
    assert list(_pieces(["<think>x. y.</think>A. ", "B"])) == ["A.", "B"]
    assert list(_pieces(["Dr. No"])) == ["Dr.", "No"]  # simple splitter


def test_pieces_cut_long_sentences_at_clauses():
    text = "Yes, so you can easily view full tab names, manage them, and free up space."
    assert list(_pieces([text])) == [
        "Yes, so you can easily view full tab names,",
        "manage them, and free up space.",
    ]
    # streamed: a clause mark waits for the whitespace after it; 39,5 is a number
    deltas = ["It was 39,5 percent", ",", " then", " more."]
    assert list(_pieces(deltas)) == ["It was 39,5 percent,", "then more."]
    assert list(_pieces(["One, two, three"])) == ["One, two, three"]


def test_llm_client_streams_sse_sentences():
    body = (
        'data: {"choices":[{"delta":{"content":"Good "}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"morning. How"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":" are you?"}}]}\n\n'
        "data: [DONE]\n\n"
    )
    seen = []

    def handle(req):
        import json

        seen.append(json.loads(req.content)["stream"])
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=body
        )

    class Target:
        def current(self):
            return "http://w", "t"

        def invalidate(self, kind):
            pass

    c = LlmClient(Target(), LlmSettings(), transport=httpx.MockTransport(handle))
    assert list(c.translate_stream("dzień dobry, jak się masz", "pl")) == [
        "Good morning.",
        "How are you?",
    ]
    assert seen == [True]


class MetaLlm(SlowLlm):
    """Streams a chat reply instead of a translation; records stream close."""

    def __init__(self, sentences):
        super().__init__(0, sentences=sentences)
        self.closed = False

    def translate_stream(self, text, source_lang, context=(), check=None, next_text=""):
        try:
            yield from super().translate_stream(text, source_lang, context)
        finally:
            self.closed = True


def _run_one(llm, text):
    played, events = [], []
    asr = SlowAsr([AsrResult(text, "pl")], 0)
    tts = SlowTts(0)
    ts, speak, capture = system(asr, llm, tts, lambda a, sr: played.append(a[0]))
    ts.set_status_callback(events.append)
    assert ts.start()
    try:
        speak()
        assert wait_for(
            lambda: any(e.get("status") == "translation_rejected" for e in events)
        )
        stats = ts.get_stats()
        context = list(ts._translation_context)
    finally:
        ts.stop()
    return tts.calls, played, events, stats, context


OWNER_META_REPLY = [
    "The content is missing.",
    "Please provide the Polish text you would like me to translate into"
    " English, and I will give you a natural-sounding translation with any"
    " necessary context-based corrections for speech-recognition errors.",
]


@pytest.mark.parametrize("text", ["медіа", "медіа контент"])
def test_meta_reply_to_short_source_never_reaches_tts(text):
    llm = MetaLlm(OWNER_META_REPLY)
    tts_calls, played, events, stats, context = _run_one(llm, text)
    assert tts_calls == [] and played == []  # not even "The content is missing."
    assert stats["llm_rejected"] == 1
    assert context == []
    assert llm.closed
    rejected = [e for e in events if e.get("status") == "translation_rejected"]
    assert rejected[0]["reason"] == "too_long"
    assert rejected[0]["original_text"] == text
    assert not any(e.get("status") == "translation_complete" for e in events)


def test_long_source_streams_until_the_guard_rejects():
    text = "dzisiaj rano poszedłem do sklepu"  # 5 words -> at most 15 out
    llm = MetaLlm(
        [
            "This morning I went to the shop.",
            "Please provide the rest of the Polish text you would like translated.",
            "Never reached.",
        ]
    )
    tts_calls, _, _, stats, context = _run_one(llm, text)
    assert tts_calls == ["This morning I went to the shop."]
    assert stats["llm_rejected"] == 1 and context == []
    assert llm.closed


@pytest.mark.parametrize("text", [".", "...", "…", "-"])
def test_asr_output_without_words_never_reaches_llm(text):
    # live 2026-10-31: whisper returned punctuation for a short word; the LLM
    # answered "I don't see any Polish speech transcript ... please provide"
    played = []
    llm = MetaLlm(["must not be called"])
    asr = SlowAsr([AsrResult(text, "pl")], 0)
    tts = SlowTts(0)
    ts, speak, capture = system(asr, llm, tts, lambda a, sr: played.append(a[0]))
    assert ts.start()
    try:
        speak()
        assert wait_for(lambda: ts.get_stats()["asr_no_words"] == 1)
        time.sleep(0.2)
        assert llm.calls == [] and tts.calls == [] and played == []
    finally:
        ts.stop()


OTHER_SOURCE = (
    "Відкрийте файл PDF і натисніть примітки виберіть стиль і колір ручки або"
    " маркера."
)


def test_long_translation_plays_every_sentence_even_when_next_phrase_comes():
    # live test 2026-10-31 §1: a 3-sentence reply was shown whole but heard
    # only up to "Okay, you impressed us, but"; the edge must hand every
    # sentence to playback, in order, and the next phrase must not cut it
    played = []

    def sink(audio, sr):
        time.sleep(0.05)  # playback takes time; next phrase arrives meanwhile
        played.append(audio[0])

    reply = [
        "Okay, you impressed us, but there are still problems.",
        "The real reason was found: the media model answered correctly.",
        "It fails to recognize it when returning text without any letters at all.",
    ]
    # one unit each (a sentence end cuts a unit, no clause mark inside)
    source = (
        "Добре вразивіш ви але все одно з проблемами бо справжня причина"
        " знайшлася і модель відповідала правильно на слові медіа."
    )
    asr = SlowAsr([AsrResult(source, "uk"), AsrResult(OTHER_SOURCE, "uk")], 0.0)
    llm = SlowLlm(0.0, sentences=reply)
    tts = SlowTts(0.0)
    ts, speak, capture = system(asr, llm, tts, sink)
    assert ts.start()
    try:
        speak()
        time.sleep(0.03)
        speak()
        assert wait_for(lambda: len(played) == 6, timeout=5)
    finally:
        ts.stop()
    assert tts.calls == reply + reply  # SlowLlm answers both phrases alike
    assert played == [float(i) for i in range(1, 7)]
    assert ts.get_stats()["playback_short"] == 0


def test_playback_that_returns_early_is_reported():
    asr = SlowAsr([AsrResult("raz dwa trzy cztery.", "pl")], 0.0)
    llm = SlowLlm(0.0, sentences=["One two three four."])

    class LongTts(SlowTts):
        def synthesize(self, text, voice=None, speed=None):
            self.calls.append(text)
            return np.zeros(24000, dtype=np.float32), 24000  # 1 s of audio

    played = []
    ts, speak, capture = system(asr, llm, LongTts(0.0), lambda a, sr: played.append(1))
    assert ts.start()
    try:
        speak()
        assert wait_for(lambda: played)
    finally:
        ts.stop()
    assert ts.get_stats()["playback_short"] == 1  # sink returned at once


def test_llm_is_warmed_up_when_the_pipeline_starts():
    events = []

    class WarmLlm(SlowLlm):
        warmups = 0

        def warm_up(self):
            WarmLlm.warmups += 1

    asr = SlowAsr([], 0.0)
    ts, speak, capture = system(asr, WarmLlm(0.0), SlowTts(0.0), lambda a, sr: None)
    ts.set_status_callback(events.append)
    assert ts.start()
    try:
        assert wait_for(lambda: "llm" in ts.get_stats()["warmup"])
    finally:
        ts.stop()
    w = ts.get_stats()["warmup"]["llm"]
    assert WarmLlm.warmups == 1
    assert w["reason"] == "start" and w["url"] == "http://llm" and w["error"] is None
    assert any(e.get("status") == "warmup" and e["kind"] == "llm" for e in events)


def test_failed_warm_up_is_reported_and_the_stage_keeps_working():
    from src.clients import ServiceError

    class ColdLlm(SlowLlm):
        def warm_up(self):
            raise ServiceError("llm", "timeout", "cold")

    played = []
    asr = SlowAsr([AsrResult("raz.", "pl")], 0.0)
    ts, speak, capture = system(
        asr, ColdLlm(0.0), SlowTts(0.0), lambda a, sr: played.append(1)
    )
    assert ts.start()
    try:
        speak()
        assert wait_for(lambda: played)
    finally:
        ts.stop()
    assert "cold" in ts.get_stats()["warmup"]["llm"]["error"]


def test_cyrillic_in_translation_is_rejected_as_foreign_script():
    llm = MetaLlm(['Press the right mouse button ("Niшки") and select it.'])
    tts_calls, played, events, stats, context = _run_one(
        llm, "натисніть правою кнопкою Нишки і виберіть."
    )
    assert tts_calls == [] and played == [] and context == []
    rejected = [e for e in events if e.get("status") == "translation_rejected"]
    assert rejected[0]["reason"] == "foreign_script"
    assert stats["llm_rejected"] == 1
