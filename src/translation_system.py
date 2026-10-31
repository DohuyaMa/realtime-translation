"""Edge coordinator: mic -> VadStream -> rt-asr -> LLM -> rt-tts -> playback.

Staged pipeline (owner 2026-10-31): every stage is its own worker thread with
a bounded FIFO queue - ASR of phrase n+1 runs while the LLM translates n, TTS
synthesizes n-1 and n-2 plays. One thread per stage keeps the order (phrase
sequence numbers in logs/timing). A full downstream queue blocks the stage
(backpressure, logged + ``backpressure`` status event); the only drop is the
explicit, logged drop-oldest policy of the segment queue in translate mode.
The LLM output is streamed: each finished sentence goes to TTS at once and
playback starts with the first synthesized sentence.

Capture and pause segmentation run locally; ASR/TTS go through endpoint pools
and translation through an app session on an llama worker (ordered worker
list with failover, no Django). In transcribe mode long chunks are sent to
rt-asr with batch priority and no worker session is opened.
"""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import numpy as np
from loguru import logger

from .audio.vad_stream import VadStream, make_vad
from .clients import (
    LlmClient,
    LlmTarget,
    Directory,
    ServiceError,
    SpeechPools,
    WorkerSessions,
    build_pools,
    discover,
    endpoints,
)
from .clients.discovery import choices
from .core.prompt import render_asr_prompt
from .core.runtime import KINDS, EdgeSettings, LlmSettings
from .segmentation import Segment, SegmenterConfig

CAPTURE_RATE = 48000
BLOCKSIZE = 1024
ASR_RATE = 16000
TRANSLATE_QUEUE_MAX = 5
STAGE_QUEUE_MAX = 4
PLAYBACK_QUEUE_MAX = 8
# recognized words kept for {{previous_text}} (asr.previous_words <= this)
PREVIOUS_WORDS_MAX = 1000

# LlmSettings fields that only shape the prompt/request; everything else
# changes where/how the LLM is reached and requires a restart.
_LLM_LIVE_FIELDS = (
    "model",
    "max_tokens_cap",
    "temperature",
    "context_pairs",
    "system_prompt",
    "user_template",
    "words_per_request",
)

_STOP = object()


@dataclass
class _Phrase:
    """One speech segment travelling through the stages, with its timing."""

    seq: int
    t_end: float  # end of speech (segment emitted by the VAD)
    segment: Optional[Segment] = None
    text: str = ""
    src: Optional[str] = None
    asr_ms: Optional[float] = None
    llm_first_ms: Optional[float] = None
    llm_ms: Optional[float] = None
    tts_first_ms: Optional[float] = None
    e2e_ms: Optional[float] = None
    pieces: int = 0  # sentences sent to TTS
    played: bool = False


@dataclass
class _Sentence:
    phrase: _Phrase
    text: str
    t_ready: float  # when the LLM produced it


def _ms(seconds: float) -> float:
    return round(seconds * 1000.0, 1)


def default_llm_target(
    settings: EdgeSettings, directory: Directory
) -> Optional[LlmTarget]:
    workers = endpoints("llm", settings, directory)
    if not workers:
        return None
    return WorkerSessions(workers, settings.llm.session_ttl_s)


def _default_vad_stream(cfg: SegmenterConfig) -> VadStream:
    return VadStream(make_vad(), cfg)


class _SoundDeviceCapture:
    """48 kHz mono float32 microphone stream (sounddevice)."""

    def __init__(self, on_block: Callable[[np.ndarray], None], device=None) -> None:
        import sounddevice as sd

        def _cb(indata, frames, cb_time, status):
            if status:
                logger.debug("sounddevice input status: {}", status)
            on_block(indata[:, 0].copy())

        self._stream = sd.InputStream(
            samplerate=CAPTURE_RATE,
            channels=1,
            dtype="float32",
            blocksize=BLOCKSIZE,
            callback=_cb,
            device=device,
        )

    def start(self) -> None:
        self._stream.start()

    def stop(self) -> None:
        self._stream.stop()

    def close(self) -> None:
        self._stream.close()


def _find_device(name: Optional[str], kind: str) -> Optional[int]:
    if not name:
        return None
    import sounddevice as sd

    for d in sd.query_devices():
        if d[f"max_{kind}_channels"] > 0 and d["name"] == name:
            return d["index"]
    logger.warning("preferred {} device {!r} not found", kind, name)
    return None


class TranslationSystem:
    def __init__(
        self,
        settings: EdgeSettings,
        *,
        directory_factory: Callable[[EdgeSettings], Directory] = discover,
        pools_factory: Callable[[EdgeSettings, Directory], SpeechPools] = build_pools,
        llm_target_factory: Callable[
            [EdgeSettings, Directory], Optional[LlmTarget]
        ] = default_llm_target,
        vad_stream_factory: Callable[
            [SegmenterConfig], VadStream
        ] = _default_vad_stream,
        llm_factory: Callable[[LlmTarget, LlmSettings], Any] = LlmClient,
        capture_factory: Optional[Callable[[Callable[[np.ndarray], None]], Any]] = None,
        playback_sink: Optional[Callable[[np.ndarray, int], None]] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self._directory_factory = directory_factory
        self._pools_factory = pools_factory
        self._llm_target_factory = llm_target_factory
        self._vad_stream_factory = vad_stream_factory
        self._llm_factory = llm_factory
        self._capture_factory = capture_factory or self._default_capture
        self._playback_sink = playback_sink or self._default_sink

        self.input_device_name: Optional[str] = None
        self.output_device_name: Optional[str] = None
        self.status_callback: Optional[Callable[[Dict], None]] = None
        self.last_error: Optional[str] = None
        self.is_running = False

        self._lock = threading.Lock()
        self._vad_lock = threading.Lock()
        self._vad: Optional[VadStream] = None
        self._mode = settings.effective_mode()
        self._chunk_queue = self._new_chunk_queue()
        self._session_held = False
        self._stream: Any = None
        self._clock = clock
        self._llm_q: queue.Queue = queue.Queue(maxsize=STAGE_QUEUE_MAX)
        self._tts_q: queue.Queue = queue.Queue(maxsize=STAGE_QUEUE_MAX)
        self._playback_queue: queue.Queue = queue.Queue(maxsize=PLAYBACK_QUEUE_MAX)
        self._seq = 0
        self._timing: Dict[str, Any] = {}
        self._backpressure: Dict[str, int] = {"llm": 0, "tts": 0, "playback": 0}
        self._dropped = 0
        self._audio_q: queue.Queue = queue.Queue()
        self._capturing = False
        self._threads: Dict[str, threading.Thread] = {}

        self._recognized: deque = deque(maxlen=PREVIOUS_WORDS_MAX)
        self._translation_context: deque = deque(maxlen=settings.llm.context_pairs)
        self._pending_recognized: List[str] = []
        self._pending_translated: List[str] = []
        self._audio_level_input = 0.0
        self._segments_processed = 0
        self._pipeline_errors = 0
        self._llm_requests = 0
        self._llm_errors = 0
        self._llm_time = 0.0

        self._build_backends()

    # -- wiring -------------------------------------------------------------

    def _build_backends(self) -> None:
        self._directory = self._directory_factory(self.settings)
        self._directory.start_periodic()
        self._pools = self._pools_factory(self.settings, self._directory)
        self._llm_target = self._llm_target_factory(self.settings, self._directory)
        self._llm = (
            self._llm_factory(self._llm_target, self.settings.llm)
            if self._llm_target is not None
            else None
        )

    def _close_backends(self) -> None:
        self._directory.stop_periodic()
        for obj in (self._llm, self._pools):
            close = getattr(obj, "close", None)
            if close:
                try:
                    close()
                except Exception as e:  # pragma: no cover - best effort
                    logger.debug("close failed: {}", e)

    @property
    def pools(self) -> SpeechPools:
        return self._pools

    @property
    def llm(self) -> Optional[LlmClient]:
        return self._llm

    @property
    def llm_target(self) -> Optional[LlmTarget]:
        return self._llm_target

    @property
    def mode(self) -> str:
        return self._mode

    def _segmenter_config(self, settings: EdgeSettings) -> SegmenterConfig:
        return getattr(settings.segmentation, settings.effective_mode())

    def _default_capture(self, on_block):
        return _SoundDeviceCapture(
            on_block, _find_device(self.input_device_name, "input")
        )

    def _default_sink(self, audio: np.ndarray, sample_rate: int) -> None:
        import sounddevice as sd

        device = _find_device(self.output_device_name, "output")
        sd.play(audio, samplerate=sample_rate, device=device, blocking=True)

    # -- public controls ----------------------------------------------------

    def set_status_callback(self, cb: Optional[Callable[[Dict], None]]) -> None:
        self.status_callback = cb

    def set_devices(self, input_name: Optional[str], output_name: Optional[str]):
        self.input_device_name = input_name
        self.output_device_name = output_name

    def _emit(self, event: Dict) -> None:
        if self.status_callback:
            try:
                self.status_callback(event)
            except Exception as e:
                logger.exception("status callback failed: {}", e)

    def _fail(self, service: str, detail: str) -> None:
        self.last_error = f"{service}: {detail}"
        self._emit({"status": "error", "service": service, "error": self.last_error})

    def start(self) -> bool:
        if self.is_running:
            return True
        self.last_error = None
        self._mode = self.settings.effective_mode()
        if self._mode == "translate":
            if self._llm is None:
                self._fail("llm", "not configured")
                return False
            if self._pools.tts is None:
                self._fail("tts", "not configured")
                return False
            if isinstance(self._llm_target, WorkerSessions):
                try:
                    self._llm_target.start()
                except ServiceError as e:
                    detail = str(e)
                    prefix = f"{e.service}: "
                    if detail.startswith(prefix):
                        detail = detail[len(prefix) :]
                    self._fail("llm", detail)
                    return False
                self._session_held = True
                self._emit(
                    {"status": "llm_session", "session": self._session_snapshot()}
                )

        self._vad = self._vad_stream_factory(self._segmenter_config(self.settings))
        self._chunk_queue = self._new_chunk_queue()
        self._audio_q = queue.Queue()
        self._llm_q = queue.Queue(maxsize=STAGE_QUEUE_MAX)
        self._tts_q = queue.Queue(maxsize=STAGE_QUEUE_MAX)
        self._playback_queue = queue.Queue(maxsize=PLAYBACK_QUEUE_MAX)
        self._recognized.clear()
        self._translation_context.clear()
        try:  # open the mic synchronously so a failure aborts the start
            self._stream = self._capture_factory(self._on_block)
            self._stream.start()
        except Exception as e:
            logger.exception("cannot open microphone: {}", e)
            self._stream = None
            self._close_session()
            self._fail("capture", str(e))
            return False
        self.is_running = True
        self._capturing = True
        self._threads = {
            name: threading.Thread(target=fn, daemon=True, name=f"rt-{name}")
            for name, fn in (
                ("asr", self._chunk_worker_loop),
                ("llm", self._llm_worker),
                ("tts", self._tts_worker),
                ("playback", self._playback_worker),
                ("capture", self._pipeline_loop),
            )
        }
        for t in self._threads.values():
            t.start()
        logger.info("translation system started: mode={}", self._mode)
        return True

    def stop(self) -> None:
        if not self.is_running:
            return
        # 1. stop capture, feed what is buffered, flush pending segments
        self._capturing = False
        capture = self._threads.get("capture")
        if capture:
            capture.join(timeout=5.0)
        with self._vad_lock:
            segments = self._vad.flush() if self._vad else []
        for segment in segments:
            self._enqueue_segment(segment)
        # 2. drain the segment queue
        cfg = self._segmenter_config(self.settings)
        self._wait_drained(max(60.0, cfg.hard_max_ms / 1000.0))
        # 3. stop the stages: _STOP travels asr -> llm -> tts -> playback
        #    behind the queued work, so every phrase is finished and played
        self._chunk_queue.put(_STOP)
        for name in ("asr", "llm", "tts", "playback"):
            t = self._threads.get(name)
            if t:
                t.join(timeout=60.0)
                if t.is_alive():
                    logger.warning("{} stage still busy after 60 s at stop", name)
        self.is_running = False
        self._threads = {}
        # 4. close the worker session
        self._close_session()
        logger.info(
            "translation system stopped: {} segments, {} errors",
            self._segments_processed,
            self._pipeline_errors,
        )

    def _close_session(self) -> None:
        if not self._session_held:
            return
        self._session_held = False
        try:
            self._llm_target.stop()
        except Exception as e:
            logger.warning("closing the worker session failed: {}", e)

    def cleanup(self) -> None:
        self.stop()
        self._close_backends()

    def _new_chunk_queue(self) -> queue.Queue:
        # transcribe never drops chunks; translate keeps only the freshest
        maxsize = TRANSLATE_QUEUE_MAX if self._mode == "translate" else 0
        return queue.Queue(maxsize=maxsize)

    def _wait_drained(self, timeout_s: float) -> None:
        q = self._chunk_queue
        with q.all_tasks_done:
            if not q.all_tasks_done.wait_for(
                lambda: q.unfinished_tasks == 0, timeout=timeout_s
            ):
                logger.warning("segment queue not drained after {:.0f}s", timeout_s)

    def apply_settings(self, settings: EdgeSettings) -> None:
        old = self.settings
        if self._needs_restart(old, settings):
            was_running = self.is_running
            self.stop()
            self._close_backends()
            self.settings = settings
            self._translation_context = deque(maxlen=settings.llm.context_pairs)
            self._build_backends()
            self._mode = settings.effective_mode()
            if was_running:
                self.start()
            return
        self.settings = settings
        if settings.llm != old.llm:
            if settings.llm.context_pairs != old.llm.context_pairs:
                self._translation_context = deque(
                    self._translation_context, maxlen=settings.llm.context_pairs
                )
            if self._llm_target is not None:
                close = getattr(self._llm, "close", None)
                self._llm = self._llm_factory(self._llm_target, settings.llm)
                if close:
                    close()
        new_cfg = self._segmenter_config(settings)
        if new_cfg != self._segmenter_config(old):
            with self._vad_lock:
                if self._vad is not None:
                    self._vad.reconfigure(new_cfg)

    @staticmethod
    def _needs_restart(old: EdgeSettings, new: EdgeSettings) -> bool:
        if old.effective_mode() != new.effective_mode():
            return True
        for name in (
            "django_urls",
            "django_token_file",
            "local",
            "local_token_file",
            "allowed_services",
            "use",
            "timeouts",
        ):
            if getattr(old, name) != getattr(new, name):
                return True
        o, n = old.llm, new.llm
        return any(
            getattr(o, f) != getattr(n, f)
            for f in o.__dataclass_fields__
            if f not in _LLM_LIVE_FIELDS
        )

    def enqueue_playback(self, audio: np.ndarray, sample_rate: int) -> None:
        """External audio for the playback stage (played in arrival order)."""
        self._put(self._playback_queue, (None, audio, int(sample_rate)), "playback")

    def _put(self, q: queue.Queue, item: Any, stage: str) -> None:
        """Hand work to ``stage``; a full queue blocks (backpressure), visibly."""
        try:
            q.put_nowait(item)
            return
        except queue.Full:
            pass
        self._backpressure[stage] += 1
        logger.warning("{} stage is behind (queue full): upstream waits", stage)
        self._emit({"status": "backpressure", "stage": stage})
        q.put(item)

    # -- threads ------------------------------------------------------------

    def _on_block(self, block: np.ndarray) -> None:
        self._audio_q.put(block)

    def _pipeline_loop(self) -> None:
        stream, self._stream = self._stream, None
        try:
            while self._capturing:
                try:
                    self._feed(self._audio_q.get(timeout=0.05))
                except queue.Empty:
                    continue
        finally:
            for op in ("stop", "close"):
                try:
                    getattr(stream, op)()
                except Exception as e:
                    logger.debug("capture {} failed: {}", op, e)
        while True:  # feed what was captured before the stream closed
            try:
                self._feed(self._audio_q.get_nowait())
            except queue.Empty:
                break

    def _feed(self, block: np.ndarray) -> None:
        with self._vad_lock:
            segments = self._vad.feed_block(block)
            self._audio_level_input = min(self._vad.last_level() * 5.0, 1.0)
        for segment in segments:
            self._enqueue_segment(segment)

    def _enqueue_segment(self, segment: Segment) -> None:
        self._seq += 1
        item = _Phrase(self._seq, self._clock(), segment)
        q = self._chunk_queue
        while True:
            try:
                q.put_nowait(item)
                return
            except queue.Full:
                try:
                    old = q.get_nowait()
                    q.task_done()
                    # explicit policy: translate mode keeps the freshest speech
                    self._dropped += 1
                    logger.warning(
                        "segment queue full - dropped oldest segment #{}",
                        getattr(old, "seq", "?"),
                    )
                    self._emit({"status": "dropped", "stage": "asr"})
                except queue.Empty:
                    pass

    def _chunk_worker_loop(self) -> None:
        q = self._chunk_queue
        while True:
            item = q.get()
            try:
                if item is _STOP:
                    self._llm_q.put(_STOP)
                    return
                self._process_segment(item)
            except Exception as e:
                logger.exception("pipeline error: {}", e)
                self._pipeline_errors += 1
                self._fail("pipeline", type(e).__name__)
            finally:
                q.task_done()

    def _llm_worker(self) -> None:
        while True:
            item = self._llm_q.get()
            if item is _STOP:
                self._tts_q.put(_STOP)
                return
            try:
                self._translate(item)
            except Exception as e:
                logger.exception("llm stage error: {}", e)
                self._pipeline_errors += 1
                self._fail("pipeline", type(e).__name__)

    def _tts_worker(self) -> None:
        while True:
            item = self._tts_q.get()
            if item is _STOP:
                self._playback_queue.put(_STOP)
                return
            try:
                self._synthesize(item)
            except Exception as e:
                logger.exception("tts stage error: {}", e)
                self._pipeline_errors += 1
                self._fail("pipeline", type(e).__name__)

    def _playback_worker(self) -> None:
        while True:
            item = self._playback_queue.get()
            if item is _STOP:
                return
            phrase, audio, sr = item
            if phrase is not None and not phrase.played:
                phrase.played = True
                phrase.e2e_ms = _ms(self._clock() - phrase.t_end)
                self._record_timing(phrase)
            try:
                self._playback_sink(audio, sr)
            except Exception as e:
                logger.exception("playback error: {}", e)

    def _record_timing(self, phrase: _Phrase) -> None:
        t = {
            "seq": phrase.seq,
            "asr_ms": phrase.asr_ms,
            "llm_ms": phrase.llm_first_ms,
            "llm_total_ms": phrase.llm_ms,
            "tts_ms": phrase.tts_first_ms,
            "e2e_ms": phrase.e2e_ms,
        }
        self._timing = t
        logger.info(
            "phrase #{}: asr {} ms, llm first sentence {} ms (total {}),"
            " tts first {} ms, end of speech -> playback {} ms",
            phrase.seq,
            phrase.asr_ms,
            phrase.llm_first_ms,
            phrase.llm_ms,
            phrase.tts_first_ms,
            phrase.e2e_ms,
        )
        self._emit({"status": "timing", "timing": t})

    # -- processing ---------------------------------------------------------

    def _service_error(self, e: ServiceError) -> None:
        self._pipeline_errors += 1
        logger.warning("{} (phrase skipped)", e)
        self.last_error = f"{e.service}: {e.kind}"
        self._emit(
            {
                "status": "error",
                "service": e.service,
                "kind": e.kind,
                "error": self.last_error,
            }
        )

    def _process_segment(self, phrase: _Phrase) -> None:
        if len(phrase.segment.audio) == 0:
            return
        if self._mode == "translate":
            self._recognize(phrase)
        else:
            self._transcribe_segment(phrase.segment)
        self._segments_processed += 1

    def _language(self) -> Optional[str]:
        lang = self.settings.source_language
        return None if lang == "auto" else lang

    def _asr_prompt(self) -> Optional[str]:
        asr = self.settings.asr
        return render_asr_prompt(
            asr.prompt, list(self._recognized), asr.previous_words, self._language()
        )

    def _transcribe_segment(self, segment: Segment) -> None:
        audio = segment.audio
        timeout_s = max(60.0, len(audio) / ASR_RATE)
        try:
            result = self._pools.asr.call(
                lambda c: c.transcribe(
                    audio,
                    language=self._language(),
                    prompt=self._asr_prompt(),
                    priority="batch",
                    timeout_s=timeout_s,
                )
            )
        except ServiceError as e:
            self._service_error(e)
            return
        text = (result.text or "").strip()
        if not text:
            return
        self._recognized.extend(text.split())
        with self._lock:
            self._pending_recognized.append(text)
        self._emit({"status": "transcription_complete", "original_text": text})

    def _recognize(self, phrase: _Phrase) -> None:
        """ASR stage (translate mode): hand the text to the LLM stage."""
        lang = self._language()
        prompt = self._asr_prompt()
        t0 = self._clock()
        try:
            result = self._pools.asr.call(
                lambda c: c.transcribe(
                    phrase.segment.audio,
                    language=lang,
                    prompt=prompt,
                    priority="realtime",
                )
            )
        except ServiceError as e:
            self._service_error(e)
            return
        phrase.asr_ms = _ms(self._clock() - t0)
        text = (result.text or "").strip()
        if not text:
            return
        self._recognized.extend(text.split())
        with self._lock:
            self._pending_recognized.append(text)
        phrase.src = lang or result.language
        if not phrase.src:
            logger.warning("source language unknown - skipping translation")
            return
        phrase.text = text
        phrase.segment = None  # audio no longer needed
        self._put(self._llm_q, phrase, "llm")

    def _translate(self, phrase: _Phrase) -> None:
        """LLM stage: stream the translation, each sentence on to TTS."""
        t0 = self._clock()
        self._llm_requests += 1
        sentences: List[str] = []
        try:
            for sentence in self._llm.translate_stream(
                phrase.text, phrase.src, context=list(self._translation_context)
            ):
                now = self._clock()
                if phrase.llm_first_ms is None:
                    phrase.llm_first_ms = _ms(now - t0)
                sentences.append(sentence)
                phrase.pieces += 1
                self._put(self._tts_q, _Sentence(phrase, sentence, now), "tts")
        except ServiceError as e:
            self._llm_errors += 1
            self._service_error(e)
            return
        except ValueError as e:  # unsupported source language
            self._llm_errors += 1
            self._pipeline_errors += 1
            logger.warning("translation skipped: {}", e)
            self._fail("llm", "language")
            return
        finally:
            self._llm_time += self._clock() - t0
        phrase.llm_ms = _ms(self._clock() - t0)
        translation = " ".join(sentences)
        self._translation_context.append((phrase.text, translation))
        with self._lock:
            self._pending_translated.append(translation)
        self._emit(
            {
                "status": "translation_complete",
                "original_text": phrase.text,
                "translated_text": translation,
            }
        )

    def _synthesize(self, item: _Sentence) -> None:
        """TTS stage: one sentence at a time, in order, on to playback."""
        tts = self.settings.tts
        try:
            audio, sr = self._pools.tts.call(
                lambda c: c.synthesize(item.text, tts.voice, tts.speed)
            )
        except ServiceError as e:
            self._service_error(e)
            return
        phrase = item.phrase
        if phrase.tts_first_ms is None:
            phrase.tts_first_ms = _ms(self._clock() - item.t_ready)
        self._put(self._playback_queue, (phrase, audio, int(sr)), "playback")

    # -- stats --------------------------------------------------------------

    def _session_snapshot(self) -> Optional[dict]:
        snapshot = getattr(self._llm_target, "snapshot", None)
        return snapshot() if callable(snapshot) else None

    def get_stats(self) -> Dict:
        tts = self._pools.tts
        stats: Dict[str, Any] = {
            "running": self.is_running,
            "mode": self._mode,
            "source_language": self.settings.source_language,
            "audio_level_input": self._audio_level_input,
            "segments_processed": self._segments_processed,
            "pipeline_errors": self._pipeline_errors,
            "last_error": self.last_error,
            "llm_requests": self._llm_requests,
            "llm_errors": self._llm_errors,
            "llm_avg_latency_s": (
                round(self._llm_time / self._llm_requests, 3)
                if self._llm_requests
                else 0.0
            ),
            "discovery": self._directory.source,
            "active": {
                "asr": self._pools.asr.active(),
                "tts": tts.active() if tts is not None else None,
            },
            "session": self._session_snapshot(),
            "timing": dict(self._timing),
            "queues": {
                "asr": self._chunk_queue.qsize(),
                "llm": self._llm_q.qsize(),
                "tts": self._tts_q.qsize(),
                "playback": self._playback_queue.qsize(),
            },
            "backpressure": dict(self._backpressure),
            "dropped_segments": self._dropped,
            "use": {k: getattr(self.settings.use, k) for k in KINDS},
            "choices": {
                k: [
                    {"node": e.node, "url": e.url, "local": e.local}
                    for e in choices(k, self.settings, self._directory)
                ]
                for k in KINDS
            },
        }
        with self._lock:
            stats["pending_recognized"] = self._pending_recognized
            stats["pending_translated"] = self._pending_translated
            self._pending_recognized = []
            self._pending_translated = []
        return stats
