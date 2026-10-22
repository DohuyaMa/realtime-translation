"""Edge coordinator: mic -> VadStream -> rt-asr -> LLM -> rt-tts -> playback.

Capture and pause segmentation run locally; ASR/TTS go through endpoint pools
and translation through an LLM target (Django lease or a static URL). In
transcribe mode long chunks are sent to rt-asr with batch priority and no lease
is taken.
"""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from typing import Any, Callable, Dict, List, Optional

import numpy as np
from loguru import logger

from .audio.vad_stream import VadStream, make_vad
from .clients import (
    LeaseKeeper,
    LlmClient,
    LlmTarget,
    ServiceError,
    SpeechPools,
    StaticTarget,
    build_pools,
)
from .core.runtime import EdgeSettings, LlmSettings
from .segmentation import Segment, SegmenterConfig

CAPTURE_RATE = 48000
BLOCKSIZE = 1024
ASR_RATE = 16000
TRANSLATE_QUEUE_MAX = 5
PLAYBACK_QUEUE_MAX = 8
PROMPT_HISTORY = 3

# LlmSettings fields that only shape the prompt/request; everything else
# changes where/how the LLM is reached and requires a restart.
_LLM_LIVE_FIELDS = ("model", "max_tokens_cap", "temperature", "context_pairs")

_STOP = object()


def default_llm_target(llm: LlmSettings) -> Optional[LlmTarget]:
    source = llm.source()
    if source == "lease":
        return LeaseKeeper(llm, None)
    if source == "direct":
        return StaticTarget(llm.direct_url or "", llm.direct_token_file)
    return None


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
        pools_factory: Callable[[EdgeSettings], SpeechPools] = build_pools,
        llm_target_factory: Callable[
            [LlmSettings], Optional[LlmTarget]
        ] = default_llm_target,
        vad_stream_factory: Callable[
            [SegmenterConfig], VadStream
        ] = _default_vad_stream,
        llm_factory: Callable[[LlmTarget, LlmSettings], Any] = LlmClient,
        capture_factory: Optional[Callable[[Callable[[np.ndarray], None]], Any]] = None,
        playback_sink: Optional[Callable[[np.ndarray, int], None]] = None,
    ) -> None:
        self.settings = settings
        self._pools_factory = pools_factory
        self._llm_target_factory = llm_target_factory
        self._vad_stream_factory = vad_stream_factory
        self._llm_factory = llm_factory
        self._capture_factory = capture_factory or self._default_capture
        self._playback_sink = playback_sink or self._default_sink

        self.tts_voice: Optional[str] = None
        self.tts_speed: Optional[float] = None
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
        self._lease_held = False
        self._stream: Any = None
        self._playback_queue: queue.Queue = queue.Queue(maxsize=PLAYBACK_QUEUE_MAX)
        self._audio_q: queue.Queue = queue.Queue()
        self._capturing = False
        self._threads: Dict[str, threading.Thread] = {}

        self._recognized: deque = deque(maxlen=PROMPT_HISTORY)
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
        self._pools = self._pools_factory(self.settings)
        self._llm_target = self._llm_target_factory(self.settings.llm)
        self._llm = (
            self._llm_factory(self._llm_target, self.settings.llm)
            if self._llm_target is not None
            else None
        )

    def _close_backends(self) -> None:
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

    def set_tts(self, voice: Optional[str], speed: Optional[float]) -> None:
        self.tts_voice = voice
        self.tts_speed = speed

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
            if isinstance(self._llm_target, LeaseKeeper):
                try:
                    self._llm_target.start()
                except ServiceError as e:
                    detail = str(e)
                    prefix = f"{e.service}: "
                    if detail.startswith(prefix):
                        detail = detail[len(prefix) :]
                    self._fail("llm", detail)
                    return False
                self._lease_held = True
                self._emit({"status": "llm_lease", "lease": self._lease_snapshot()})

        self._vad = self._vad_stream_factory(self._segmenter_config(self.settings))
        self._chunk_queue = self._new_chunk_queue()
        self._audio_q = queue.Queue()
        self._playback_queue = queue.Queue(maxsize=PLAYBACK_QUEUE_MAX)
        self._recognized.clear()
        self._translation_context.clear()
        try:  # open the mic synchronously so a failure aborts the start
            self._stream = self._capture_factory(self._on_block)
            self._stream.start()
        except Exception as e:
            logger.exception("cannot open microphone: {}", e)
            self._stream = None
            self._release_lease()
            self._fail("capture", str(e))
            return False
        self.is_running = True
        self._capturing = True
        self._threads = {
            name: threading.Thread(target=fn, daemon=True, name=f"rt-{name}")
            for name, fn in (
                ("worker", self._chunk_worker_loop),
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
        # 3. stop the workers
        self.is_running = False
        self._enqueue_segment(_STOP)
        for name in ("worker", "playback"):
            t = self._threads.get(name)
            if t:
                t.join(timeout=5.0)
        self._threads = {}
        # 4. release the lease
        self._release_lease()
        logger.info(
            "translation system stopped: {} segments, {} errors",
            self._segments_processed,
            self._pipeline_errors,
        )

    def _release_lease(self) -> None:
        if not self._lease_held:
            return
        self._lease_held = False
        try:
            self._llm_target.stop()
        except Exception as e:
            logger.warning("lease release failed: {}", e)

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
        for name in ("asr_urls", "tts_urls", "api_token_file", "timeouts"):
            if getattr(old, name) != getattr(new, name):
                return True
        o, n = old.llm, new.llm
        return any(
            getattr(o, f) != getattr(n, f)
            for f in o.__dataclass_fields__
            if f not in _LLM_LIVE_FIELDS
        )

    def enqueue_playback(self, audio: np.ndarray, sample_rate: int) -> None:
        try:
            self._playback_queue.put_nowait((audio, int(sample_rate)))
        except queue.Full:
            logger.warning("playback queue full - dropping audio")

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

    def _enqueue_segment(self, item) -> None:
        q = self._chunk_queue
        while True:
            try:
                q.put_nowait(item)
                return
            except queue.Full:
                try:
                    q.get_nowait()
                    q.task_done()
                    logger.warning("segment queue full - dropped oldest segment")
                except queue.Empty:
                    pass

    def _chunk_worker_loop(self) -> None:
        q = self._chunk_queue
        while True:
            item = q.get()
            try:
                if item is _STOP:
                    return
                self._process_segment(item)
            except Exception as e:
                logger.exception("pipeline error: {}", e)
                self._pipeline_errors += 1
                self._fail("pipeline", type(e).__name__)
            finally:
                q.task_done()

    def _playback_worker(self) -> None:
        while self.is_running:
            try:
                audio, sr = self._playback_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                self._playback_sink(audio, sr)
            except Exception as e:
                logger.exception("playback error: {}", e)

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

    def _process_segment(self, segment: Segment) -> None:
        if len(segment.audio) == 0:
            return
        if self._mode == "translate":
            self._translate_segment(segment)
        else:
            self._transcribe_segment(segment)
        self._segments_processed += 1

    def _language(self) -> Optional[str]:
        lang = self.settings.source_language
        return None if lang == "auto" else lang

    def _transcribe_segment(self, segment: Segment) -> None:
        audio = segment.audio
        timeout_s = max(60.0, len(audio) / ASR_RATE)
        try:
            result = self._pools.asr.call(
                lambda c: c.transcribe(
                    audio,
                    language=self._language(),
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
        with self._lock:
            self._pending_recognized.append(text)
        self._emit({"status": "transcription_complete", "original_text": text})

    def _translate_segment(self, segment: Segment) -> None:
        lang = self._language()
        prompt = " ".join(self._recognized) or None
        try:
            result = self._pools.asr.call(
                lambda c: c.transcribe(
                    segment.audio, language=lang, prompt=prompt, priority="realtime"
                )
            )
        except ServiceError as e:
            self._service_error(e)
            return
        text = (result.text or "").strip()
        if not text:
            return
        self._recognized.append(text)
        with self._lock:
            self._pending_recognized.append(text)
        src = lang or result.language
        if not src:
            logger.warning("source language unknown - skipping translation")
            return

        t0 = time.monotonic()
        self._llm_requests += 1
        try:
            translation = self._llm.translate(
                text, src, context=list(self._translation_context)
            )
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
            self._llm_time += time.monotonic() - t0
        self._translation_context.append((text, translation))
        with self._lock:
            self._pending_translated.append(translation)

        try:
            audio, sr = self._pools.tts.call(
                lambda c: c.synthesize(translation, self.tts_voice, self.tts_speed)
            )
        except ServiceError as e:
            self._service_error(e)
        else:
            self.enqueue_playback(audio, sr)
        self._emit(
            {
                "status": "translation_complete",
                "original_text": text,
                "translated_text": translation,
            }
        )

    # -- stats --------------------------------------------------------------

    def _lease_snapshot(self) -> Optional[dict]:
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
            "pinned": {
                "asr": self._pools.asr.pinned_url,
                "tts": tts.pinned_url if tts is not None else None,
            },
            "lease": self._lease_snapshot(),
        }
        with self._lock:
            stats["pending_recognized"] = self._pending_recognized
            stats["pending_translated"] = self._pending_translated
            self._pending_recognized = []
            self._pending_translated = []
        return stats
