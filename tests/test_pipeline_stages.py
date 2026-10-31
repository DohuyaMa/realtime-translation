"""Staged pipeline (owner 2026-10-31): ASR of n+1 overlaps LLM of n and TTS
of n-1, order kept, LLM sentences go to TTS/playback as they come."""

import threading
import time

import httpx
import pytest
import numpy as np

from src.clients.asr import AsrResult
from src.clients.llm import LlmClient, _sentences
from src.core.runtime import LlmSettings
from test_translation_system import (
    FakeAsr,
    FakeCapture,
    FakePools,
    FakeTts,
    FakeVad,
    StaticTarget,
    seg,
    translate_settings,
    wait_for,
)
from src.clients import Directory
from src.translation_system import TranslationSystem

STAGE_S = 0.1


class SlowAsr(FakeAsr):
    def __init__(self, results, delay, clock=None):
        super().__init__(results)
        self.delay, self.clock = delay, clock

    def transcribe(self, audio, **kw):
        self._advance()
        return super().transcribe(audio, **kw)

    def _advance(self):
        if self.clock is not None:
            self.clock.t += self.delay
        else:
            time.sleep(self.delay)


class SlowLlm:
    def __init__(self, delay, clock=None, sentences=None, gate=None):
        self.delay, self.clock = delay, clock
        self.sentences = sentences
        self.gate = gate
        self.calls = []

    def translate_stream(self, text, source_lang, context=()):
        self.calls.append(text)
        if self.clock is not None:
            self.clock.t += self.delay
        else:
            time.sleep(self.delay)
        for i, s in enumerate(self.sentences or [f"EN({text})"]):
            if i and self.gate is not None:  # later sentences wait for playback
                assert self.gate.wait(3)
            yield s

    def close(self):
        pass


class SlowTts(FakeTts):
    def __init__(self, delay, clock=None):
        super().__init__()
        self.delay, self.clock = delay, clock

    def synthesize(self, text, voice=None, speed=None):
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
    vads = []
    capture = FakeCapture()

    def vad_factory(cfg):
        v = FakeVad(cfg)
        vads.append(v)
        return v

    ts = TranslationSystem(
        translate_settings(),
        directory_factory=lambda s: Directory(),
        pools_factory=lambda s, d: FakePools(asr, tts),
        llm_target_factory=lambda s, d: StaticTarget("http://llm"),
        vad_stream_factory=vad_factory,
        llm_factory=lambda t, s: llm,
        capture_factory=capture,
        playback_sink=sink,
        clock=clock,
    )
    return ts, vads, capture


def run_three(asr_delay=STAGE_S, llm_delay=STAGE_S, tts_delay=STAGE_S):
    played = []
    asr = SlowAsr([AsrResult(f"p{i}", "pl") for i in (1, 2, 3)], asr_delay)
    llm = SlowLlm(llm_delay)
    tts = SlowTts(tts_delay)
    ts, vads, capture = system(asr, llm, tts, lambda a, sr: played.append(a[0]))
    assert ts.start()
    try:
        t0 = time.monotonic()
        vads[-1].pending.extend([seg(), seg(), seg()])
        capture.push()
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
    assert tts_calls == ["EN(p1)", "EN(p2)", "EN(p3)"]
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
    ts, vads, capture = system(asr, llm, tts, sink)
    assert ts.start()
    try:
        vads[-1].pending.append(seg())
        capture.push()
        assert wait_for(lambda: len(played) == 2)
    finally:
        ts.stop()
    assert tts.calls == ["One.", "Two."]  # TTS per sentence


def test_stage_timing_with_fake_clock():
    clock = Clock()
    played = []
    # cyborg numbers measured 2026-10-31: asr 3.8 s, llm 0.4 s, tts 4.7 s
    asr = SlowAsr([AsrResult("raz", "pl")], 3.8, clock)
    llm = SlowLlm(0.4, clock)
    tts = SlowTts(4.7, clock)
    ts, vads, capture = system(asr, llm, tts, lambda a, sr: played.append(1), clock)
    assert ts.start()
    try:
        vads[-1].pending.append(seg())
        capture.push()
        assert wait_for(lambda: played)
        assert wait_for(lambda: ts.get_stats()["timing"])
        t = ts.get_stats()["timing"]
    finally:
        ts.stop()
    assert (t["asr_ms"], t["llm_ms"], t["tts_ms"]) == (3800.0, 400.0, 4700.0)
    assert t["e2e_ms"] == 8900.0 and t["seq"] == 1


def test_full_stage_queue_is_backpressure_not_drop():
    from src import translation_system as tsmod

    played = []
    n = tsmod.STAGE_QUEUE_MAX + 3
    asr = SlowAsr([AsrResult(f"p{i}", "pl") for i in range(n)], 0.0)
    llm = SlowLlm(0.0)
    tts = SlowTts(0.05)  # slowest stage
    events = []
    ts, vads, capture = system(asr, llm, tts, lambda a, sr: played.append(a[0]))
    ts.set_status_callback(events.append)
    assert ts.start()
    try:
        for _ in range(n):  # one segment per block: not dropped by the asr queue
            vads[-1].pending.append(seg())
            capture.push()
            time.sleep(0.005)
        assert wait_for(lambda: len(played) == n, timeout=5)
    finally:
        ts.stop()
    stats = ts.get_stats()
    assert played == [float(i) for i in range(1, n + 1)]
    assert stats["dropped_segments"] == 0
    assert stats["backpressure"]["tts"] >= 1
    assert {"status": "backpressure", "stage": "tts"} in events


def test_sentences_from_stream_deltas():
    deltas = ["Hello wor", "ld. How", " are you? Fi", "ne"]
    assert list(_sentences(deltas)) == ["Hello world.", "How are you?", "Fine"]
    assert list(_sentences(["<think>x. y.</think>A. ", "B"])) == ["A.", "B"]
    assert list(_sentences(["Dr. No"])) == ["Dr.", "No"]  # simple splitter


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

    def translate_stream(self, text, source_lang, context=()):
        try:
            yield from super().translate_stream(text, source_lang, context)
        finally:
            self.closed = True


def _run_one(llm, text):
    played, events = [], []
    asr = SlowAsr([AsrResult(text, "pl")], 0)
    tts = SlowTts(0)
    ts, vads, capture = system(asr, llm, tts, lambda a, sr: played.append(a[0]))
    ts.set_status_callback(events.append)
    assert ts.start()
    try:
        vads[-1].pending.append(seg())
        capture.push()
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
