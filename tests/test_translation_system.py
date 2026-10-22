"""TranslationSystem on endpoint pools, lease-based LLM and pause segmentation."""

import threading
import time

import numpy as np

from src.clients import LeaseKeeper, ServiceError, StaticTarget
from src.clients.asr import AsrResult
from src.core.runtime import EdgeSettings, LlmSettings, SegmentationSettings
from src.segmentation import Segment, SegmenterConfig
from src.translation_system import TranslationSystem, default_llm_target


def seg(seconds: float = 1.0) -> Segment:
    audio = np.ones(int(seconds * 16000), dtype=np.int16)
    return Segment(audio=audio, start_s=0.0, end_s=seconds, reason="pause")


class FakeAsr:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def transcribe(self, audio, sample_rate=16000, language=None, prompt=None, **kw):
        self.calls.append(
            {"n": len(audio), "language": language, "prompt": prompt, **kw}
        )
        r = self.results.pop(0) if self.results else AsrResult("", None)
        if isinstance(r, Exception):
            raise r
        return r


class FakeTts:
    def __init__(self):
        self.calls = []

    def synthesize(self, text, voice=None, speed=None):
        self.calls.append((text, voice, speed))
        return np.zeros(240, dtype=np.float32), 24000


class FakePool:
    def __init__(self, client, url="http://x"):
        self.client = client
        self.pinned_url = url

    def call(self, fn):
        return fn(self.client)

    def close(self):
        pass


class FakePools:
    def __init__(self, asr, tts=None):
        self.asr = FakePool(asr, "http://asr")
        self.tts = FakePool(tts, "http://tts") if tts is not None else None

    def by_name(self):
        return {"asr": self.asr, "tts": self.tts}

    def close(self):
        pass


class FakeLlm:
    def __init__(self, errors=()):
        self.errors = list(errors)
        self.calls = []

    def translate(self, text, source_lang, context=()):
        self.calls.append((text, source_lang, list(context)))
        if self.errors:
            err = self.errors.pop(0)
            if err is not None:
                raise err
        return f"EN({text})"

    def close(self):
        pass


class FakeKeeper(LeaseKeeper):
    def __init__(self, error=None):  # no super(): no network, no threads
        self.error = error
        self.events = []

    def start(self):
        self.events.append("start")
        if self.error:
            raise self.error

    def stop(self):
        self.events.append("stop")

    def snapshot(self):
        return {"service": "llm-a", "base_url": "http://gpu"}

    def current(self):
        return "http://gpu", "tok"

    def invalidate(self, kind):
        pass


class FakeVad:
    def __init__(self, cfg):
        self.cfg = cfg
        self.pending = []
        self.flush_segments = []
        self.reconfigured = []
        self.blocks = 0

    def feed_block(self, block):
        self.blocks += 1
        out, self.pending = self.pending, []
        return out

    def flush(self):
        out, self.flush_segments = self.flush_segments, []
        return out

    def reconfigure(self, cfg):
        self.reconfigured.append(cfg)
        self.cfg = cfg

    def last_level(self):
        return 0.1


class FakeCapture:
    def __init__(self):
        self.on_block = None
        self.closed = False
        self.error = None
        self.log = []

    def __call__(self, on_block):
        if self.error:
            raise self.error
        self.on_block = on_block
        self.log.append("capture")
        return self

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        self.closed = True

    def push(self, n=1024):
        self.on_block(np.zeros(n, dtype=np.float32))


class Sink:
    def __init__(self):
        self.played = []

    def __call__(self, audio, sr):
        self.played.append((len(audio), sr))


def translate_settings(**kw):
    llm = kw.pop("llm", LlmSettings(direct_url="http://llm", context_pairs=3))
    return EdgeSettings(mode="translate", tts_urls=("http://tts",), llm=llm, **kw)


def make(settings, asr_results=(), target=None, llm=None, tts=True):
    asr = FakeAsr(asr_results)
    tts_client = FakeTts() if tts else None
    pools = FakePools(asr, tts_client)
    vads = []
    capture = FakeCapture()
    sink = Sink()
    events = []
    llm = llm or FakeLlm()

    def vad_factory(cfg):
        v = FakeVad(cfg)
        vads.append(v)
        return v

    ts = TranslationSystem(
        settings,
        pools_factory=lambda s: pools,
        llm_target_factory=lambda s: target or StaticTarget("http://llm", None),
        vad_stream_factory=vad_factory,
        llm_factory=lambda t, s: llm,
        capture_factory=capture,
        playback_sink=sink,
    )
    ts.set_status_callback(events.append)
    return ts, pools, llm, vads, capture, sink, events


def wait_for(pred, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return False


def feed(ts, vads, capture, *segments):
    vads[-1].pending.extend(segments)
    capture.push()


def test_translate_flow_calls_asr_llm_tts_and_playback():
    ts, pools, llm, vads, capture, sink, events = make(
        translate_settings(), [AsrResult("cześć", "pl")]
    )
    ts.set_tts("am_adam", 1.1)
    assert ts.start() is True
    try:
        feed(ts, vads, capture, seg())
        assert wait_for(lambda: sink.played)
    finally:
        ts.stop()
    call = pools.asr.client.calls[0]
    assert call["language"] is None and call["priority"] == "realtime"
    assert call["prompt"] is None
    assert llm.calls == [("cześć", "pl", [])]
    assert pools.tts.client.calls == [("EN(cześć)", "am_adam", 1.1)]
    assert sink.played == [(240, 24000)]
    assert {
        "status": "translation_complete",
        "original_text": "cześć",
        "translated_text": "EN(cześć)",
    } in events
    stats = ts.get_stats()
    assert stats["llm_requests"] == 1 and stats["llm_errors"] == 0
    assert stats["pinned"] == {"asr": "http://asr", "tts": "http://tts"}


def test_translate_start_acquires_lease_and_stop_releases():
    keeper = FakeKeeper()
    ts, _, _, _, capture, _, events = make(translate_settings(), target=keeper)
    capture.log = keeper.events
    assert ts.start() is True
    assert keeper.events == ["start", "capture"]  # lease before the mic opens
    assert {
        "status": "llm_lease",
        "lease": {"service": "llm-a", "base_url": "http://gpu"},
    } in events
    assert ts.get_stats()["lease"] == {"service": "llm-a", "base_url": "http://gpu"}
    ts.stop()
    assert keeper.events == ["start", "capture", "stop"]


def test_translate_start_fails_cleanly_without_lease():
    keeper = FakeKeeper(ServiceError("llm", "unreachable", "no lease available"))
    ts, _, _, _, capture, _, _ = make(translate_settings(), target=keeper)
    assert ts.start() is False
    assert ts.last_error.startswith("llm:")
    assert ts.is_running is False
    assert capture.on_block is None  # capture never opened


def test_llm_context_carries_previous_pairs():
    ts, _, llm, vads, capture, sink, _ = make(
        translate_settings(source_language="uk"),
        [AsrResult("один", "uk"), AsrResult("два", "uk")],
    )
    ts.start()
    try:
        feed(ts, vads, capture, seg(), seg())
        assert wait_for(lambda: len(sink.played) == 2)
    finally:
        ts.stop()
    assert llm.calls[0][2] == []
    assert llm.calls[1] == ("два", "uk", [("один", "EN(один)")])


def test_transcribe_mode_uses_batch_priority_scaled_timeout_and_no_lease():
    keeper = FakeKeeper()
    settings = EdgeSettings(
        mode="transcribe", llm=LlmSettings(django_urls=("http://d",))
    )
    ts, pools, llm, vads, capture, sink, events = make(
        settings, [AsrResult("довгий текст", "uk")], target=keeper
    )
    assert ts.start() is True
    try:
        feed(ts, vads, capture, seg(120.0))
        assert wait_for(lambda: events)
    finally:
        ts.stop()
    call = pools.asr.client.calls[0]
    assert call["priority"] == "batch" and call["timeout_s"] == 120.0
    assert keeper.events == []
    assert llm.calls == [] and pools.tts.client.calls == [] and sink.played == []
    assert vads[0].cfg == settings.segmentation.transcribe
    assert events == [
        {"status": "transcription_complete", "original_text": "довгий текст"}
    ]


def test_stop_flushes_and_drains_transcribe_chunk():
    settings = EdgeSettings(mode="transcribe")
    ts, pools, _, vads, capture, _, events = make(
        settings, [AsrResult("середина"), AsrResult("кінець")]
    )
    gate = threading.Event()
    asr = pools.asr.client
    original = asr.transcribe

    def slow(*a, **kw):
        gate.wait(0.3)
        return original(*a, **kw)

    asr.transcribe = slow
    ts.start()
    vads[0].flush_segments = [seg(300.0), seg(30.0)]
    ts.stop()
    assert [c["n"] for c in asr.calls] == [300 * 16000, 30 * 16000]
    assert [c["timeout_s"] for c in asr.calls] == [300.0, 60.0]
    assert [e["original_text"] for e in events] == ["середина", "кінець"]


def test_service_error_skips_phrase_and_continues():
    llm = FakeLlm(errors=[ServiceError("llm", "http", "HTTP 500"), None])
    ts, _, llm, vads, capture, sink, events = make(
        translate_settings(),
        [AsrResult("раз", "uk"), AsrResult("два", "uk")],
        llm=llm,
    )
    ts.start()
    try:
        feed(ts, vads, capture, seg(), seg())
        assert wait_for(lambda: len(sink.played) == 1)
    finally:
        ts.stop()
    assert ts.last_error == "llm: http"
    assert [c[0] for c in llm.calls] == ["раз", "два"]
    assert llm.calls[1][2] == []  # failed phrase not added to context
    assert any(e["status"] == "error" for e in events)
    stats = ts.get_stats()
    assert stats["llm_errors"] == 1 and stats["llm_requests"] == 2


def test_apply_settings_reconfigures_segmenter_live():
    settings = translate_settings()
    ts, pools, _, vads, _, _, _ = make(settings)
    ts.start()
    try:
        new_cfg = SegmenterConfig(end_silence_ms=500)
        new = EdgeSettings(
            mode=settings.mode,
            tts_urls=settings.tts_urls,
            llm=settings.llm,
            source_language="pl",
            segmentation=SegmentationSettings(translate=new_cfg),
        )
        ts.apply_settings(new)
        assert vads[0].reconfigured == [new_cfg]
        assert len(vads) == 1 and ts.is_running  # no restart
        assert ts.settings.source_language == "pl"
    finally:
        ts.stop()


def test_apply_settings_restarts_when_urls_change():
    settings = translate_settings()
    ts, _, _, vads, _, _, _ = make(settings)
    ts.start()
    try:
        ts.apply_settings(
            EdgeSettings(
                mode="translate",
                asr_urls=("http://other",),
                tts_urls=settings.tts_urls,
                llm=settings.llm,
            )
        )
        assert len(vads) == 2 and ts.is_running
    finally:
        ts.stop()


def test_enqueue_playback_plays_external_audio():
    ts, _, _, _, _, sink, _ = make(EdgeSettings(mode="transcribe"))
    ts.start()
    try:
        ts.enqueue_playback(np.zeros(100, dtype=np.float32), 22050)
        assert wait_for(lambda: sink.played)
    finally:
        ts.stop()
    assert sink.played == [(100, 22050)]


def test_default_llm_target_by_source():
    assert default_llm_target(LlmSettings()) is None
    assert isinstance(
        default_llm_target(LlmSettings(direct_url="http://llm")), StaticTarget
    )
    assert isinstance(
        default_llm_target(LlmSettings(django_urls=("http://d",))), LeaseKeeper
    )


def test_translate_queue_drops_oldest_segments():
    ts, _, _, _, _, _, _ = make(translate_settings())
    for i in range(7):
        ts._enqueue_segment(seg(i + 1))
    assert ts._chunk_queue.qsize() == 5
    assert len(ts._chunk_queue.queue[0].audio) == 3 * 16000


def test_capture_failure_aborts_start_and_releases_lease():
    keeper = FakeKeeper()
    ts, _, _, _, capture, _, events = make(translate_settings(), target=keeper)
    capture.error = OSError("no mic")
    assert ts.start() is False
    assert keeper.events == ["start", "stop"]
    assert ts.is_running is False
    assert ts.last_error == "capture: no mic"
    assert events[-1]["status"] == "error"
    ts.stop()  # no-op
    assert keeper.events == ["start", "stop"]


def test_llm_context_evicts_beyond_context_pairs():
    texts = ["a", "b", "c", "d", "e"]
    ts, _, llm, vads, capture, sink, _ = make(
        translate_settings(source_language="uk"),
        [AsrResult(t, "uk") for t in texts],
    )
    ts.start()
    try:
        feed(ts, vads, capture, *[seg() for _ in texts])
        assert wait_for(lambda: len(sink.played) == 5)
    finally:
        ts.stop()
    pairs = [(t, f"EN({t})") for t in texts]
    assert llm.calls[3][2] == pairs[:3]
    assert llm.calls[4][2] == pairs[1:4]


def test_start_clears_prompt_and_translation_context():
    ts, pools, llm, vads, capture, sink, _ = make(
        translate_settings(source_language="uk"),
        [AsrResult("один", "uk"), AsrResult("два", "uk")],
    )
    for _ in range(2):
        ts.start()
        try:
            feed(ts, vads, capture, seg())
            assert wait_for(lambda: len(sink.played) == 1)
        finally:
            ts.stop()
        sink.played.clear()
    assert llm.calls[1] == ("два", "uk", [])
    assert pools.asr.client.calls[1]["prompt"] is None


def test_unsupported_language_sets_llm_language_error():
    llm = FakeLlm(errors=[ValueError("unsupported source language 'de'")])
    ts, _, _, vads, capture, sink, events = make(
        translate_settings(), [AsrResult("hallo", "de")], llm=llm
    )
    ts.start()
    try:
        feed(ts, vads, capture, seg())
        assert wait_for(lambda: ts.last_error is not None)
    finally:
        ts.stop()
    assert ts.last_error == "llm: language"
    assert events[-1]["status"] == "error" and sink.played == []
