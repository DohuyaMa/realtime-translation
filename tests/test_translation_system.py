"""TranslationSystem on endpoint pools, worker-session LLM and the rt-asr /
rt-asr WS stream (spec 2026-10-31): stream finals -> units -> LLM -> TTS."""

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
from src.core.runtime import AsrRequestSettings, EdgeSettings, LlmSettings, LocalUrls
from src.translation_system import (
    TTS_WARMUP_TEXT,
    TranslationSystem,
    default_llm_target,
)


class FakeModelService:
    """/status + /model/load of rt-asr / rt-phonetic / rt-tts (warm-up)."""

    state = "ready"
    loads = 0

    def get_status(self):
        return {"model": {"state": self.state}}

    def load_model(self, timeout_s=None):
        self.loads += 1
        self.state = "ready"
        return {}


class FakeAsr(FakeModelService):
    """rt-asr behind the pool: only /status and /model/load (recognition
    goes through the stream)."""

    def __init__(self, results=()):
        self.calls = []


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
        self.prompt = prompt
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

    def audio(self, t0, t1):
        return np.zeros(int((t1 - t0) * 16000), dtype=np.int16)

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

    def call_once(self, fn):
        return fn(self.client)

    def close(self):
        pass


class FakePools:
    def __init__(self, asr, tts=None, phonetic=None):
        self.asr = FakePool(asr, "http://asr")
        self.tts = FakePool(tts, "http://tts") if tts is not None else None
        self.phonetic = (
            FakePool(phonetic, "http://phonetic") if phonetic is not None else None
        )

    def by_name(self):
        return {"asr": self.asr, "tts": self.tts}

    def close(self):
        pass


class FakeLlm:
    def __init__(self, errors=()):
        self.errors = list(errors)
        self.calls = []

    def translate(self, text, source_lang, context=(), sound=None, next_text=""):
        self.calls.append((text, source_lang, list(context)))
        self.nexts = getattr(self, "nexts", []) + [next_text]
        if self.errors:
            err = self.errors.pop(0)
            if err is not None:
                raise err
        return f"EN({text})"

    def translate_stream(
        self,
        text,
        source_lang,
        context=(),
        sound=None,
        next_text="",
        insist_complete=False,
    ):
        yield self.translate(text, source_lang, context, sound, next_text)

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


def final(text, start=0.0, step=0.3, eos=True, language="uk", seq=1):
    """A stream final: the words of text from start, step s each."""
    words = [
        {
            "word": w,
            "start": round(start + i * step, 3),
            "end": round(start + (i + 1) * step, 3),
            "conf": 0.9,
        }
        for i, w in enumerate(text.split())
    ]
    end = words[-1]["end"] if words else start
    return {
        "type": "final",
        "seq": seq,
        "text": text,
        "words": words,
        "start": start,
        "end": end,
        "eos": eos,
        "language": language,
        "audio_end": end + 0.6,
    }


def say(streams, text, start=0.0, kind="asr", **kw):
    streams[kind].emit(final(text, start, **kw))


def make(settings, target=None, llm=None, tts=True, streams=None):
    asr = FakeAsr()
    tts_client = FakeTts() if tts else None
    pools = FakePools(asr, tts_client)
    capture = FakeCapture()
    sink = Sink()
    events = []
    llm = llm or FakeLlm()
    streams = streams or FakeStreams()
    ts = TranslationSystem(
        settings,
        directory_factory=lambda s: Directory(),
        pools_factory=lambda s, d: pools,
        llm_target_factory=lambda s, d: target or StaticTarget("http://llm"),
        llm_factory=lambda t, s: llm,
        capture_factory=capture,
        playback_sink=sink,
        stream_factory=streams,
    )
    ts.set_status_callback(events.append)
    return ts, pools, llm, streams, capture, sink, events


def wait_for(pred, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return False


def test_translate_flow_stream_units_llm_tts_and_playback():
    from src.core.runtime import TtsRequestSettings

    ts, pools, llm, streams, capture, sink, events = make(
        translate_settings(
            tts=TtsRequestSettings(voice="am_adam", speed=1.1),
            asr=AsrRequestSettings(prompt="NixOS"),
        ),
    )
    assert ts.start() is True
    try:
        capture.push()
        assert wait_for(lambda: streams["asr"].fed == 342)  # 1024 at 48 kHz
        say(streams, "cześć", language="pl")
        assert wait_for(lambda: sink.played)
    finally:
        ts.stop()
    asr = streams["asr"]
    assert asr.language is None and asr.prompt == "NixOS" and asr.stopped
    assert [s.kind for s in streams.opened] == ["asr"]  # the only stream
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
    assert stats["units_processed"] == 1 and stats["detected_language"] == "pl"
    assert stats["active"] == {
        "asr": {"url": "http://asr"},
        "phonetic": None,
        "tts": {"url": "http://tts"},
    }
    assert stats["discovery"] == "none"
    assert stats["use"] == {"llm": None, "asr": None, "tts": None, "phonetic": None}
    assert {c["url"] for c in stats["choices"]["tts"]} == {"http://tts"}
    assert stats["latency"]["e2e_ms"]["n"] == 1


def test_units_follow_sentence_ends_and_next_carries_the_rest():
    ts, _, llm, streams, capture, sink, _ = make(
        translate_settings(source_language="uk")
    )
    ts.start()
    try:
        # one final with a sentence end inside: the first sentence goes at
        # once with the rest (committed + partial) as NEXT
        streams["asr"].emit({"type": "partial", "text": "dalsze słowa", "start": 2.0})
        say(streams, "Pierwsze zdanie. Drugie bez", eos=False)
        assert wait_for(lambda: len(sink.played) == 1)
        assert ts.get_stats()["source_tail"] == "Drugie bez dalsze słowa"
        streams["asr"].emit({"type": "endpoint", "at": 1.5})
        assert wait_for(lambda: len(sink.played) == 2)
    finally:
        ts.stop()
    assert [c[0] for c in llm.calls] == ["Pierwsze zdanie.", "Drugie bez"]
    assert llm.nexts == ["Drugie bez dalsze słowa", ""]
    assert ts.get_stats()["source_tail"] == ""


def test_stop_translates_the_uncommitted_rest():
    ts, _, llm, streams, capture, sink, _ = make(
        translate_settings(source_language="uk")
    )
    ts.start()
    say(streams, "słowa bez końca", eos=False)
    time.sleep(0.1)
    assert llm.calls == []
    ts.stop()
    assert [c[0] for c in llm.calls] == ["słowa bez końca"] and sink.played


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
    ts, _, _, streams, capture, _, _ = make(translate_settings(), target=keeper)
    assert ts.start() is False
    assert ts.last_error.startswith("llm:")
    assert ts.is_running is False
    assert capture.on_block is None  # capture never opened
    assert streams.opened == []


def test_llm_context_carries_previous_pairs():
    ts, _, llm, streams, capture, sink, _ = make(
        translate_settings(source_language="uk")
    )
    ts.start()
    try:
        say(streams, "odyn.")
        say(streams, "dva.", start=1.0)
        assert wait_for(lambda: len(sink.played) == 2)
    finally:
        ts.stop()
    assert llm.calls[0][2] == []
    assert llm.calls[1] == ("dva.", "uk", [("odyn.", "EN(odyn.)")])


def test_transcribe_mode_lines_from_the_stream_and_no_session():
    keeper = FakeKeeper()
    settings = EdgeSettings(mode="transcribe", local=LocalUrls(llm="http://l"))
    ts, pools, llm, streams, capture, sink, events = make(settings, target=keeper)
    assert ts.start() is True
    try:
        say(streams, "Довгий текст. І далі", eos=False)
        assert wait_for(lambda: _phrase_events(events))
    finally:
        ts.stop()
    assert keeper.events == []
    assert [s.kind for s in streams.opened] == ["asr"]
    assert llm.calls == [] and pools.tts.client.calls == [] and sink.played == []
    assert [e["original_text"] for e in _phrase_events(events)] == [
        "Довгий текст.",
        "І далі",  # stop: the rest is one more line
    ]


def test_service_error_skips_unit_and_continues():
    llm = FakeLlm(errors=[ServiceError("llm", "http", "HTTP 500"), None])
    ts, _, llm, streams, capture, sink, events = make(translate_settings(), llm=llm)
    ts.start()
    try:
        say(streams, "raz.")
        say(streams, "dva.", start=1.0)
        assert wait_for(lambda: len(sink.played) == 1)
    finally:
        ts.stop()
    assert ts.last_error == "llm: http"
    assert [c[0] for c in llm.calls] == ["raz.", "dva."]
    assert llm.calls[1][2] == []  # failed unit not added to context
    assert any(e["status"] == "error" for e in events)
    stats = ts.get_stats()
    assert stats["llm_errors"] == 1 and stats["llm_requests"] == 2


def test_asr_stream_loss_is_visible():
    ts, _, _, streams, _, _, events = make(translate_settings())
    ts.start()
    try:
        streams["asr"].emit({"type": "down", "url": "http://asr", "error": "closed"})
        assert wait_for(lambda: ts.last_error == "asr: closed")
    finally:
        ts.stop()
    assert any(e.get("service") == "asr" and e["status"] == "error" for e in events)


def test_apply_settings_live_llm_change_keeps_streams():
    settings = translate_settings()
    ts, _, _, streams, _, _, _ = make(settings)
    ts.start()
    try:
        ts.apply_settings(
            EdgeSettings(
                mode=settings.mode,
                local=settings.local,
                llm=LlmSettings(context_pairs=3, temperature=0.5),
            )
        )
        assert len(streams.opened) == 1 and ts.is_running  # no restart
    finally:
        ts.stop()


def test_apply_settings_restarts_when_urls_or_language_change():
    settings = translate_settings()
    ts, _, _, streams, _, _, _ = make(settings)
    ts.start()
    try:
        ts.apply_settings(
            EdgeSettings(
                mode="translate",
                local=LocalUrls(asr="http://other", llm="http://llm", tts="http://tts"),
                llm=settings.llm,
            )
        )
        assert len(streams.opened) == 2 and ts.is_running
        # the stream gets its language at the start
        ts.apply_settings(
            EdgeSettings(
                mode="translate",
                local=LocalUrls(asr="http://other", llm="http://llm", tts="http://tts"),
                llm=settings.llm,
                source_language="uk",
            )
        )
        assert len(streams.opened) == 3 and streams["asr"].language == "uk"
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
    ts, _, llm, streams, capture, sink, _ = make(
        translate_settings(source_language="uk")
    )
    ts.start()
    try:
        for i, t in enumerate(texts):
            say(streams, t, start=float(i))
        assert wait_for(lambda: len(sink.played) == 5)
    finally:
        ts.stop()
    pairs = [(t, f"EN({t})") for t in texts]
    assert llm.calls[3][2] == pairs[:3]
    assert llm.calls[4][2] == pairs[1:4]


def test_start_clears_ledger_and_translation_context():
    ts, pools, llm, streams, capture, sink, _ = make(
        translate_settings(source_language="uk")
    )
    for text in ("odyn.", "dva."):
        ts.start()
        try:
            say(streams, text)
            assert wait_for(lambda: len(sink.played) == 1)
        finally:
            ts.stop()
        sink.played.clear()
    assert llm.calls[1] == ("dva.", "uk", [])
    assert len(streams.opened) == 2


def test_unsupported_language_sets_llm_language_error():
    llm = FakeLlm(errors=[ValueError("unsupported source language 'de'")])
    ts, _, _, streams, capture, sink, events = make(translate_settings(), llm=llm)
    ts.start()
    try:
        say(streams, "hallo", language="de")
        assert wait_for(lambda: ts.last_error is not None)
    finally:
        ts.stop()
    assert ts.last_error == "llm: language"
    assert events[-1]["status"] == "error" and sink.played == []
