"""TranslationSystem on endpoint pools, worker-session LLM and pause segmentation."""

import threading
import time

import numpy as np

from src.clients import (
    Credential,
    Directory,
    Endpoint,
    ServiceEntry,
    ServiceError,
    WorkerSessions,
)
from src.clients.asr import AsrResult
from src.core.runtime import (
    EdgeSettings,
    LlmSettings,
    LocalUrls,
    SegmentationSettings,
)
from src.segmentation import Segment, SegmenterConfig
from src.translation_system import (
    TTS_WARMUP_TEXT,
    TranslationSystem,
    default_llm_target,
)


def seg(seconds: float = 1.0) -> Segment:
    audio = np.ones(int(seconds * 16000), dtype=np.int16)
    return Segment(audio=audio, start_s=0.0, end_s=seconds, reason="pause")


class FakeModelService:
    """/status + /model/load of rt-asr / rt-vosk / rt-tts (warm-up)."""

    state = "ready"
    loads = 0

    def get_status(self):
        return {"model": {"state": self.state}}

    def load_model(self, timeout_s=None):
        self.loads += 1
        self.state = "ready"
        return {}


class FakeAsr(FakeModelService):
    def __init__(self, results):
        self.results = list(results)
        self.calls = []
        self.warmups = 0

    def transcribe(self, audio, sample_rate=16000, language=None, prompt=None, **kw):
        self.calls.append(
            {"n": len(audio), "language": language, "prompt": prompt, **kw}
        )
        r = self.results.pop(0) if self.results else AsrResult("", None)
        if isinstance(r, Exception):
            raise r
        return r


class FakeStream:
    """A WS stream (clients.stream.StreamClient): ready at once unless
    ``hold``; the test plays the server with ``emit``."""

    def __init__(
        self,
        kind,
        pool,
        *,
        language=None,
        on_event=None,
        prompt=None,
        hold=False,
        error=None,
    ):
        self.kind, self.pool, self.language = kind, pool, language
        self.on_event = on_event
        self.hold, self.error = hold, error
        self.fed = 0
        self.started = self.stopped = False

    def start(self):
        self.started = True
        if self.error is not None:
            self.emit({"type": "failed", "error": self.error})
        elif not self.hold:
            self.ready()

    def ready(self, loaded=False):
        self.emit(
            {
                "type": "ready",
                "url": self.pool.url,
                "ms": 1.0,
                "loaded": loaded,
                "model": "m",
                "reconnect": False,
            }
        )

    def emit(self, msg):
        self.on_event(self.kind, msg)

    def feed(self, pcm):
        self.fed += len(pcm)

    def stop(self, timeout_s=None):
        self.stopped = True

    def close(self):
        self.stopped = True

    def stats(self):
        return {"url": self.pool.url, "state": "open"}


class FakeStreams:
    """stream_factory: every opened stream, by kind (latest)."""

    def __init__(self, **per_kind):
        self.per_kind = per_kind  # kind -> FakeStream kwargs
        self.opened = []

    def __call__(self, kind, pool, **kw):
        s = FakeStream(kind, pool, **kw, **self.per_kind.get(kind, {}))
        self.opened.append(s)
        return s

    def __getitem__(self, kind):
        return [s for s in self.opened if s.kind == kind][-1]


class FakeTts(FakeModelService):
    """Records synthesized sentences; the pipeline-start warm-up call
    (``warmups``) is kept apart so call lists stay about phrases."""

    def __init__(self):
        self.calls = []
        self.warmups = 0

    def synthesize(self, text, voice=None, speed=None):
        if text == TTS_WARMUP_TEXT:
            self.warmups += 1
            return np.zeros(10, dtype=np.float32), 24000
        self.calls.append((text, voice, speed))
        return np.zeros(240, dtype=np.float32), 24000


class FakePool:
    def __init__(self, client, url="http://x"):
        self.client = client
        self.url = url

    def active(self):
        return {"url": self.url}

    def call(self, fn):
        return fn(self.client)

    def close(self):
        pass


class FakePools:
    def __init__(self, asr, tts=None, vosk=None):
        self.asr = FakePool(asr, "http://asr")
        self.tts = FakePool(tts, "http://tts") if tts is not None else None
        self.vosk = FakePool(vosk, "http://vosk") if vosk is not None else None

    def by_name(self):
        return {"asr": self.asr, "tts": self.tts}

    def close(self):
        pass


class FakeLlm:
    def __init__(self, errors=()):
        self.errors = list(errors)
        self.calls = []

    def translate(self, text, source_lang, context=(), check=None):
        self.calls.append((text, source_lang, list(context)))
        if self.errors:
            err = self.errors.pop(0)
            if err is not None:
                raise err
        return f"EN({text})"

    def translate_stream(self, text, source_lang, context=(), check=None):
        yield self.translate(text, source_lang, context)

    base_url = "http://llm"

    def warm_up(self):
        self.warmups = getattr(self, "warmups", 0) + 1

    def close(self):
        pass


class StaticTarget:
    def __init__(self, url):
        self.url = url

    def current(self):
        return self.url, ""

    def invalidate(self, kind):
        pass


class FakeKeeper(WorkerSessions):
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
        return {"node": "gpu", "active": "http://gpu"}

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

    def in_speech(self):
        return self.speaking

    speaking = False


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


def _phrase_events(events):
    """Events without the pipeline-start warm-up ones."""
    return [e for e in events if e.get("status") not in ("warmup", "warmup_state")]


def translate_settings(**kw):
    llm = kw.pop("llm", LlmSettings(context_pairs=3))
    local = LocalUrls(llm="http://llm", tts="http://tts")
    return EdgeSettings(mode="translate", local=local, llm=llm, **kw)


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
        directory_factory=lambda s: Directory(),
        pools_factory=lambda s, d: pools,
        llm_target_factory=lambda s, d: target or StaticTarget("http://llm"),
        vad_stream_factory=vad_factory,
        llm_factory=lambda t, s: llm,
        capture_factory=capture,
        playback_sink=sink,
        stream_factory=FakeStreams(),
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
    from src.core.runtime import AsrRequestSettings, TtsRequestSettings

    ts, pools, llm, vads, capture, sink, events = make(
        translate_settings(
            tts=TtsRequestSettings(voice="am_adam", speed=1.1),
            asr=AsrRequestSettings(prompt="NixOS"),
        ),
        [AsrResult("cześć", "pl")],
    )
    assert ts.start() is True
    try:
        feed(ts, vads, capture, seg())
        assert wait_for(lambda: sink.played)
    finally:
        ts.stop()
    call = pools.asr.client.calls[0]
    assert call["language"] is None and call["priority"] == "realtime"
    assert call["prompt"] == "NixOS"  # user prompt, no recognized context yet
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
    assert stats["active"] == {
        "asr": {"url": "http://asr"},
        "vosk": None,
        "tts": {"url": "http://tts"},
    }
    assert stats["discovery"] == "none"
    assert stats["use"] == {"llm": None, "asr": None, "tts": None, "vosk": None}
    assert {c["url"] for c in stats["choices"]["tts"]} == {"http://tts"}


def test_translate_start_opens_session_and_stop_closes():
    keeper = FakeKeeper()
    ts, _, _, _, capture, _, events = make(translate_settings(), target=keeper)
    capture.log = keeper.events
    assert ts.start() is True
    assert keeper.events == ["start", "capture"]  # session before the mic opens
    snap = {"node": "gpu", "active": "http://gpu"}
    assert {"status": "llm_session", "session": snap} in events
    assert ts.get_stats()["session"] == snap
    ts.stop()
    assert keeper.events == ["start", "capture", "stop"]


def test_translate_start_fails_cleanly_without_worker():
    keeper = FakeKeeper(ServiceError("llm", "unreachable", "no llm worker available"))
    ts, _, _, _, capture, _, _ = make(translate_settings(), target=keeper)
    assert ts.start() is False
    assert ts.last_error.startswith("llm:")
    assert ts.is_running is False
    assert capture.on_block is None  # capture never opened


def test_llm_context_carries_previous_pairs():
    ts, _, llm, vads, capture, sink, _ = make(
        translate_settings(source_language="uk"),
        [AsrResult("odyn.", "uk"), AsrResult("dva.", "uk")],
    )
    ts.start()
    try:
        feed(ts, vads, capture, seg(), seg())
        assert wait_for(lambda: len(sink.played) == 2)
    finally:
        ts.stop()
    assert llm.calls[0][2] == []
    assert llm.calls[1] == ("dva.", "uk", [("odyn.", "EN(odyn.)")])


def test_transcribe_mode_uses_batch_priority_scaled_timeout_and_no_session():
    keeper = FakeKeeper()
    settings = EdgeSettings(mode="transcribe", local=LocalUrls(llm="http://l"))
    ts, pools, llm, vads, capture, sink, events = make(
        settings, [AsrResult("довгий текст", "uk")], target=keeper
    )
    assert ts.start() is True
    try:
        feed(ts, vads, capture, seg(120.0))
        assert wait_for(lambda: _phrase_events(events))
    finally:
        ts.stop()
    call = pools.asr.client.calls[0]
    assert call["priority"] == "batch" and call["timeout_s"] == 120.0
    assert keeper.events == []
    assert llm.calls == [] and pools.tts.client.calls == [] and sink.played == []
    assert vads[0].cfg == settings.segmentation.transcribe
    assert _phrase_events(events) == [
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
    assert [e["original_text"] for e in _phrase_events(events)] == [
        "середина",
        "кінець",
    ]


def test_service_error_skips_phrase_and_continues():
    llm = FakeLlm(errors=[ServiceError("llm", "http", "HTTP 500"), None])
    ts, _, llm, vads, capture, sink, events = make(
        translate_settings(),
        [AsrResult("raz.", "uk"), AsrResult("dva.", "uk")],
        llm=llm,
    )
    ts.start()
    try:
        feed(ts, vads, capture, seg(), seg())
        assert wait_for(lambda: len(sink.played) == 1)
    finally:
        ts.stop()
    assert ts.last_error == "llm: http"
    assert [c[0] for c in llm.calls] == ["raz.", "dva."]
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
            local=settings.local,
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
                local=LocalUrls(asr="http://other", llm="http://llm", tts="http://tts"),
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


def test_default_llm_target_from_local_and_discovery():
    assert default_llm_target(EdgeSettings(), Directory()) is None
    d = Directory(
        [ServiceEntry("llm", "remote-node", "http://remote:8080", "t")], "cache"
    )
    target = default_llm_target(EdgeSettings(), d)
    assert isinstance(target, WorkerSessions)
    assert target.urls == ["http://remote:8080"]
    local = EdgeSettings(local=LocalUrls(llm="http://127.0.0.1:8080"))
    assert default_llm_target(local, d).urls[0] == "http://127.0.0.1:8080"
    assert Endpoint and Credential  # re-exported


def test_translate_queue_drops_oldest_segments():
    ts, _, _, _, _, _, _ = make(translate_settings())
    for i in range(7):
        ts._enqueue_segment(seg(i + 1))
    assert ts._chunk_queue.qsize() == 5
    assert len(ts._chunk_queue.queue[0].segment.audio) == 3 * 16000


def test_capture_failure_aborts_start_and_closes_session():
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
    texts = ["a.", "b.", "c.", "d.", "e."]
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
        [AsrResult("odyn.", "uk"), AsrResult("dva.", "uk")],
    )
    for _ in range(2):
        ts.start()
        try:
            feed(ts, vads, capture, seg())
            assert wait_for(lambda: len(sink.played) == 1)
        finally:
            ts.stop()
        sink.played.clear()
    assert llm.calls[1] == ("dva.", "uk", [])
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
