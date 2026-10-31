"""Edge coordinator: mic -> VadStream -> rt-asr -> LLM -> rt-tts -> playback.

Staged pipeline (owner 2026-10-31): every stage is its own worker thread with
a bounded FIFO queue - ASR of phrase n+1 runs while the LLM translates n, TTS
synthesizes n-1 and n-2 plays. One thread per stage keeps the order (phrase
sequence numbers in logs/timing). A full downstream queue blocks the stage
(backpressure, logged + ``backpressure`` status event); the only drop is the
explicit, logged drop-oldest policy of the segment queue in translate mode.
The LLM output is streamed: each finished sentence goes to TTS at once and
playback starts with the first synthesized sentence.

Only complete sentences go to the LLM (live test 2026-10-31): the ASR stage
holds a recognized tail that does not end a sentence (``split_complete``) and
prepends it to the next recognized segment. The hold is bounded - it is sent
alone once no speech follows within ``end_silence_ms``, when the next segment
is dropped (no words, prompt echo, gate, ASR error), after at most
``end_silence_ms + soft_max_ms`` even while speech goes on, and never spans
more than ``hard_max_ms`` of speech - and every hold/merge/flush is logged
and counted (``asr_merged``, ``asr_held_flushed``).

Whisper echo: on noise whisper may return the previous words it was prompted
with ({{previous_text}}, live test §12 #20). Such a text is dropped before
the gate and the LLM - logged, counted (``asr_echo``), emitted - with or
without Vosk. The check lives here, not in rt-asr: rt-asr gets one opaque
rendered prompt (template + previous words) and cannot tell which
part was previous speech, while the edge knows exactly which words it sent
and owns the drop bookkeeping (counters, held-tail release).

Vosk cross-check (TASK-SYS-12): the ASR stage sends the same segment audio
to whisper and to rt-vosk in parallel. The segment-level gate
(``core.crosscheck.gate_reason``) drops a segment before the LLM only when
whisper clearly hallucinated it - Vosk heard no real word, or not one
whisper word agrees with Vosk (``vosk_gate_dropped`` / ``_disagree``);
whisper words the Vosk model cannot write (Latin terms for a uk model) do
not count, a segment of only such words passes (``vosk_gate_foreign_terms``);
otherwise the LLM gets whisper as the primary text and Vosk as a hint
(check prompts, ``Aligned`` keeps them in step through the hold/merge).
Vosk never deletes words from a passed segment. rt-vosk switched off
(``services.use.vosk = off``) or unavailable -> whisper-only with the plain
prompts; the mode and its reason are in ``get_stats()["vosk"]`` and every
change is logged/emitted. Each phrase is logged in one INFO line (whisper,
Vosk + mean conf + agreement, gate, LLM mode, translation); the opt-in
session recording (``core.session_record``) also keeps its audio.

Capture and pause segmentation run locally; ASR/TTS go through endpoint pools
and translation through an app session on an llama worker (ordered worker
list with failover, no Django). In transcribe mode long chunks are sent to
rt-asr with batch priority and no worker session is opened.
"""

from __future__ import annotations

import queue
import subprocess
from concurrent.futures import Future, ThreadPoolExecutor
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence

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
    client_factories,
    discover,
    endpoints,
)
from .clients.discovery import choices
from .clients.vosk import VoskResult
from .core.crosscheck import (
    GATE_DISAGREE,
    GATE_DROPS,
    GATE_FOREIGN_TERMS,
    Aligned,
    agreement,
    gate_reason,
    mean_conf,
)
from .core.output_guard import reject_reason
from .core.prompt import is_prompt_echo, render_asr_prompt_counted
from .core.runtime import KINDS, OFF, EdgeSettings, LlmSettings
from .core.session_record import SessionRecorder
from .segmentation import Segment, SegmenterConfig, split_complete

CAPTURE_RATE = 48000
BLOCKSIZE = 1024
ASR_RATE = 16000
TRANSLATE_QUEUE_MAX = 5
STAGE_QUEUE_MAX = 4
PLAYBACK_QUEUE_MAX = 8
# recognized words kept for {{previous_text}} (asr.previous_words <= this)
PREVIOUS_WORDS_MAX = 1000
# sources of at most this many words are translated whole before TTS gets
# anything (see _translate)
SHORT_SOURCE_WORDS = 3
# an unknown capture source makes parec exit within this time
CAPTURE_START_CHECK_S = 0.2
# TTS warm-up text (first Kokoro synthesis took 6.3 s live, 2026-10-31 §10)
TTS_WARMUP_TEXT = "Hello."
# how often the ASR stage re-checks a held incomplete text
HOLD_POLL_S = 0.05
# playback that returns this much earlier than its audio lasts is reported
PLAYBACK_SHORT_S = 0.3

# LlmSettings fields that only shape the prompt/request; everything else
# changes where/how the LLM is reached and requires a restart.
_LLM_LIVE_FIELDS = (
    "model",
    "max_tokens_cap",
    "temperature",
    "context_pairs",
    "system_prompt",
    "user_template",
    "check_system_prompt",
    "check_user_template",
    "words_per_request",
    "terms",
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
    # Vosk transcript aligned with ``text``; None = whisper-only phrase
    check: Optional[Aligned] = None
    # mean Vosk word confidence of the phrase's words (log/record only)
    vosk_conf: Optional[float] = None
    # gate_reason of a passed phrase ("agree+foreign_terms" when merged)
    gate: Optional[str] = None
    asr_ms: Optional[float] = None
    llm_first_ms: Optional[float] = None
    llm_ms: Optional[float] = None
    tts_first_ms: Optional[float] = None
    e2e_ms: Optional[float] = None
    pieces: int = 0  # sentences sent to TTS
    played: bool = False
    played_pieces: int = 0


@dataclass
class _Sentence:
    phrase: _Phrase
    text: str
    t_ready: float  # when the LLM produced it


def _fmt(value: Optional[float]) -> str:
    return "-" if value is None else f"{value:.2f}"


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


class PulseCapture:
    """48 kHz mono float32 capture from the PipeWire/Pulse source named
    ``source`` (``pactl list sources`` name; None = the default source) via
    ``parec``. Recording a named source directly - instead of switching the
    system default mic - leaves every other app's microphone alone."""

    def __init__(
        self, on_block: Callable[[np.ndarray], None], source: Optional[str] = None
    ) -> None:
        self._on_block = on_block
        self.source = source
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._stopping = False

    def _where(self) -> str:
        return self.source or "the default source"

    def start(self) -> None:
        args = [
            "parec",
            "--raw",
            "--format=float32le",
            "--channels=1",
            f"--rate={CAPTURE_RATE}",
            f"--latency-msec={int(BLOCKSIZE * 1000 / CAPTURE_RATE)}",
            "--client-name=real-time-translator",
            "--stream-name=Захоплення",
        ]
        if self.source:
            args.append(f"--device={self.source}")
        self._proc = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        try:  # an unknown source makes parec exit at once
            code = self._proc.wait(timeout=CAPTURE_START_CHECK_S)
        except subprocess.TimeoutExpired:
            code = None
        if code is not None:
            err = self._proc.stderr.read().decode(errors="replace").strip()
            raise RuntimeError(
                f"parec from {self._where()} failed (exit {code}): {err}"
            )
        self._thread = threading.Thread(target=self._read, daemon=True, name="rt-parec")
        self._thread.start()

    def _read(self) -> None:
        proc = self._proc
        size = BLOCKSIZE * 4
        while True:
            data = proc.stdout.read(size)
            if len(data) < size:
                break
            self._on_block(np.frombuffer(data, dtype=np.float32).copy())
        if not self._stopping:
            err = proc.stderr.read().decode(errors="replace").strip()
            logger.error(
                "capture from {} ended unexpectedly (exit {}): {}",
                self._where(),
                proc.poll(),
                err,
            )

    def stop(self) -> None:
        self._stopping = True
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            self._proc.wait(timeout=5.0)

    def close(self) -> None:
        if self._thread is not None:
            self._thread.join(timeout=2.0)


def _pacat_args(sample_rate: int, sink: Optional[str]) -> List[str]:
    args = [
        "pacat",
        "--playback",
        "--raw",
        "--format=float32le",
        "--channels=1",
        f"--rate={int(sample_rate)}",
        "--client-name=real-time-translator",
        "--stream-name=Переклад",
    ]
    if sink:
        args.append(f"--device={sink}")
    return args


def play_to_sinks(
    audio: np.ndarray, sample_rate: int, sinks: Sequence[Optional[str]]
) -> None:
    """Play mono float32 audio into the PipeWire/Pulse sinks named in
    ``sinks`` (``pactl list sinks`` names, e.g. ``rt_virtual_output``; None =
    the default sink) at the same time - one ``pacat`` per sink, all started
    together; returns once every sink has played it. PortAudio cannot address
    a PipeWire node by name, so this goes through ``pacat``."""
    data = np.ascontiguousarray(audio, dtype=np.float32).tobytes()
    procs = [
        (
            sink,
            subprocess.Popen(
                _pacat_args(sample_rate, sink),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            ),
        )
        for sink in sinks
    ]
    results: Dict[int, bytes] = {}

    def feed(i: int, proc: subprocess.Popen) -> None:
        results[i] = proc.communicate(data)[1]

    threads = [
        threading.Thread(target=feed, args=(i, proc), daemon=True)
        for i, (_, proc) in enumerate(procs)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    errors = [
        f"pacat to {sink or 'the default sink'} failed (exit {proc.returncode}):"
        f" {results.get(i, b'').decode(errors='replace').strip()}"
        for i, (sink, proc) in enumerate(procs)
        if proc.returncode != 0
    ]
    if errors:
        raise RuntimeError("; ".join(errors))


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
        recorder_factory: Callable[[], SessionRecorder] = SessionRecorder,
    ) -> None:
        self.settings = settings
        self._recorder_factory = recorder_factory
        # «Записувати сесію для аналізу» (settings.record_session)
        self._recorder: Optional[SessionRecorder] = None
        self._directory_factory = directory_factory
        self._pools_factory = pools_factory
        self._llm_target_factory = llm_target_factory
        self._vad_stream_factory = vad_stream_factory
        self._llm_factory = llm_factory
        self._capture_factory = capture_factory or self._default_capture
        self._playback_sink = playback_sink or self._default_sink

        self.input_device_name: Optional[str] = None
        self.output_device_name: Optional[str] = None
        # «Також чути в навушниках»: a second sink, played in sync (None = off)
        self.monitor_device_name: Optional[str] = None
        self._capture_lock = threading.Lock()
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
        self._playback_short = 0
        self._asr_no_words = 0
        self._asr_merged = 0
        self._asr_held_flushed = 0
        self._asr_echo = 0
        # Vosk cross-check (TASK-SYS-12)
        self._vosk_exec = ThreadPoolExecutor(2, thread_name_prefix="rt-vosk")
        self._vosk_state: Dict[str, Any] = {"mode": "off", "error": None}
        self._vosk_checked = 0
        self._vosk_gate_dropped = 0
        self._vosk_gate_disagree = 0  # of those: whisper and Vosk disagree
        self._vosk_gate_foreign_terms = 0  # passed: only foreign-script words
        self._vosk_whisper_only = 0
        self._vosk_after_whisper = 0
        # whisper's last detected language: Vosk's language in auto mode
        self._last_language: Optional[str] = None
        # incomplete recognized text waiting for the next segment (ASR stage)
        self._held: Optional[_Phrase] = None
        self._held_since = 0.0  # time.monotonic() when it was held
        self._held_start_s = 0.0  # stream time where the held speech began
        self._llm_warmup: Optional[Dict[str, Any]] = None
        self._tts_warmup: Optional[Dict[str, Any]] = None
        self._audio_q: queue.Queue = queue.Queue()
        # periodic Django discovery runs unless the kill switch paused it
        self._background = True
        self._capturing = False
        self._threads: Dict[str, threading.Thread] = {}

        self._recognized: deque = deque(maxlen=PREVIOUS_WORDS_MAX)
        self._translation_context: deque = deque(maxlen=settings.llm.context_pairs)
        # recognized phrases for the UI: {"text": whisper, "check": Vosk's
        # text of the same audio, None = no Vosk check for this phrase}
        self._pending_recognized: List[Dict[str, Optional[str]]] = []
        self._pending_translated: List[str] = []
        self._audio_level_input = 0.0
        self._segments_processed = 0
        self._pipeline_errors = 0
        self._llm_requests = 0
        self._llm_errors = 0
        self._llm_rejected = 0
        self._llm_time = 0.0

        self._build_backends()

    # -- wiring -------------------------------------------------------------

    def _build_backends(self) -> None:
        self._directory = self._directory_factory(self.settings)
        if self._background:
            self._directory.start_periodic()
        self._pools = self._pools_factory(self.settings, self._directory)
        self._vosk_state = self._initial_vosk_state()
        self._llm_target = self._llm_target_factory(self.settings, self._directory)
        if isinstance(self._llm_target, WorkerSessions):
            self._llm_target.on_switch = self._warm_up_async
        self._llm = (
            self._llm_factory(self._llm_target, self.settings.llm)
            if self._llm_target is not None
            else None
        )

    def _initial_vosk_state(self) -> Dict[str, Any]:
        if self.settings.use.vosk == OFF:
            return {"mode": "off", "error": "вимкнено в налаштуваннях"}
        if self._pools.vosk is None:
            return {
                "mode": "unavailable",
                "error": "rt-vosk не знайдено (немає локального і дозволених вузлів)",
            }
        return {"mode": "on", "error": None}

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
        return PulseCapture(on_block, self.input_device_name)

    def _default_sink(self, audio: np.ndarray, sample_rate: int) -> None:
        sinks = [self.output_device_name]
        if self.monitor_device_name is not None:
            sinks.append(self.monitor_device_name)
        play_to_sinks(audio, sample_rate, sinks)

    # -- public controls ----------------------------------------------------

    def set_status_callback(self, cb: Optional[Callable[[Dict], None]]) -> None:
        self.status_callback = cb

    def set_devices(
        self,
        input_name: Optional[str],
        output_name: Optional[str],
        monitor_name: Optional[str] = None,
    ) -> None:
        """Capture source, playback sink and the optional monitor sink
        (pactl names; None = default source/sink, monitor off). A new
        source while capturing switches the capture at once; a failure
        raises and keeps the old one."""
        old_input = self.input_device_name
        self.output_device_name = output_name
        self.monitor_device_name = monitor_name
        if input_name == old_input:
            return
        self.input_device_name = input_name
        if self._capturing:
            try:
                self._switch_capture()
            except Exception:
                self.input_device_name = old_input
                raise

    def _switch_capture(self) -> None:
        new = self._capture_factory(self._on_block)
        new.start()
        with self._capture_lock:
            old, self._stream = self._stream, new
        logger.info("capture switched to {}", self.input_device_name or "default")
        for op in ("stop", "close"):
            try:
                getattr(old, op)()
            except Exception as e:
                logger.warning("closing the previous capture ({}) failed: {}", op, e)

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
        self._held = None
        self._last_language = None
        if self._mode == "translate":
            v = self._vosk_state
            log = logger.info if v["mode"] == "on" else logger.warning
            log(
                "vosk cross-check: {}{}",
                v["mode"],
                f" ({v['error']}) - whisper-only translation" if v["error"] else "",
            )
        try:  # open the mic synchronously so a failure aborts the start
            self._stream = self._capture_factory(self._on_block)
            self._stream.start()
        except Exception as e:
            logger.exception("cannot open microphone: {}", e)
            self._stream = None
            self._close_session()
            self._fail("capture", str(e))
            return False
        if self._mode == "translate" and self.settings.record_session:
            self._start_recording()
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
        self._stop_recording()
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
        self._vosk_exec.shutdown(wait=False)

    # -- kill switch (standby) ---------------------------------------------

    def release_session(self) -> None:
        """Close the worker app session (idempotent): the worker goes back
        to its Django tasks."""
        self._session_held = False
        if isinstance(self._llm_target, WorkerSessions):
            self._llm_target.stop()

    def pause_background(self) -> None:
        """Stop periodic Django discovery (also across backend rebuilds)."""
        self._background = False
        self._directory.stop_periodic()

    def resume_background(self) -> None:
        self._background = True
        self._directory.start_periodic()

    def local_model_clients(self) -> Dict[str, Any]:
        """Clients of THIS host's rt-asr / rt-vosk / rt-tts, whichever
        instance is picked for the pipeline; cluster nodes are never
        included (they serve others). rt-vosk switched off in the settings
        is left alone. The caller closes them."""
        f = client_factories(self.settings)
        out: Dict[str, Any] = {}
        kinds = [("asr", f.asr), ("vosk", f.vosk), ("tts", f.tts)]
        if self.settings.use.vosk == OFF:
            kinds = [k for k in kinds if k[0] != "vosk"]
        for kind, make in kinds:
            local = [
                e for e in choices(kind, self.settings, self._directory) if e.local
            ]
            if local:
                out[kind] = make(local[0])
        return out

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
        if settings.record_session != old.record_session and self.is_running:
            if settings.record_session and self._mode == "translate":
                self._start_recording()
            else:
                self._stop_recording()
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
        try:
            while self._capturing:
                try:
                    self._feed(self._audio_q.get(timeout=0.05))
                except queue.Empty:
                    continue
        finally:
            with self._capture_lock:
                stream, self._stream = self._stream, None
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
            try:
                item = q.get(timeout=HOLD_POLL_S if self._held is not None else None)
            except queue.Empty:
                self._release_held_if_due()
                continue
            try:
                if item is _STOP:
                    self._flush_held("stop")
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
        if self._mode == "translate" and self._llm is not None:
            self._warm_up("start")
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
        if self._mode == "translate" and self._pools.tts is not None:
            self._warm_up_tts()
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
            self._play(phrase, audio, sr)

    def _play(self, phrase: Optional[_Phrase], audio: np.ndarray, sr: int) -> None:
        """Play one piece; each piece is logged with its length, and a sink
        that returns before the audio could have played is reported (live
        test 2026-10-31 §1: a reply was heard only partly)."""
        duration = len(audio) / float(sr) if sr else 0.0
        label = "external audio"
        if phrase is not None:
            phrase.played_pieces += 1
            label = f"phrase #{phrase.seq} piece {phrase.played_pieces}"
        logger.info("playback: {} ({:.2f} s audio)", label, duration)
        t0 = time.monotonic()
        try:
            self._playback_sink(audio, sr)
        except Exception as e:
            logger.exception("playback error: {}", e)
            return
        took = time.monotonic() - t0
        if took < duration - PLAYBACK_SHORT_S:
            self._playback_short += 1
            logger.warning(
                "playback: {} returned after {:.2f} s of {:.2f} s audio",
                label,
                took,
                duration,
            )

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

    def _asr_prompt(self) -> tuple[Optional[str], List[str]]:
        """(whisper prompt, the previous words substituted into it)."""
        asr = self.settings.asr
        recognized = list(self._recognized)
        prompt, used = render_asr_prompt_counted(
            asr.prompt,
            recognized,
            asr.previous_words,
            self._language(),
        )
        return prompt, recognized[len(recognized) - used :]

    def _transcribe_segment(self, segment: Segment) -> None:
        audio = segment.audio
        timeout_s = max(60.0, len(audio) / ASR_RATE)
        try:
            result = self._pools.asr.call(
                lambda c: c.transcribe(
                    audio,
                    language=self._language(),
                    prompt=self._asr_prompt()[0],
                    priority="batch",
                    timeout_s=timeout_s,
                )
            )
        except ServiceError as e:
            self._service_error(e)
            return
        text = (result.text or "").strip()
        if not self._has_words(text):
            return
        self._recognized.extend(text.split())
        with self._lock:
            self._pending_recognized.append({"text": text, "check": None})
        self._emit({"status": "transcription_complete", "original_text": text})

    def _has_words(self, text: str) -> bool:
        """ASR output with no letters ("", ".", "...", "-") is not speech:
        the LLM answers it with a chat reply ("I don't see any transcript,
        please provide ..."), so it never goes further - counted, logged."""
        if any(ch.isalpha() for ch in text):
            return True
        if text:
            self._asr_no_words += 1
            logger.info("ASR output without words skipped: {!r}", text)
        return False

    def _recognize(self, phrase: _Phrase) -> None:
        """ASR stage (translate mode): whisper and Vosk in parallel, the
        gate, then the text (with its Vosk check) on to the LLM stage."""
        lang = self._language()
        prompt, previous = self._asr_prompt()
        audio = phrase.segment.audio
        t0 = self._clock()
        # auto source language: Vosk runs in parallel in the language whisper
        # detected last; the first phrase / a language change waits for
        # whisper (see _vosk_result)
        vosk_lang = lang or self._last_language
        future = self._vosk_submit(audio, vosk_lang) if vosk_lang else None
        try:
            result = self._pools.asr.call(
                lambda c: c.transcribe(
                    audio,
                    language=lang,
                    prompt=prompt,
                    priority="realtime",
                )
            )
        except ServiceError as e:
            self._service_error(e)
            if future is not None:
                future.cancel()
            self._release_held("ASR error")
            return
        text = (result.text or "").strip()
        has_words = self._has_words(text)
        if not has_words or self._is_echo(phrase, text, previous):
            phrase.asr_ms = _ms(self._clock() - t0)
            if future is not None:
                future.cancel()
            why = "asr_echo" if has_words else "no_words"
            self._record_segment(phrase, lang or result.language, text, None, why)
            self._release_held(why)
            return
        src = lang or result.language
        if src:
            self._last_language = src
        vosk = self._vosk_result(future, audio, vosk_lang, src) if src else None
        phrase.asr_ms = _ms(self._clock() - t0)
        reason = None if vosk is None else gate_reason(text, vosk.text, src)
        self._record_segment(phrase, src, text, vosk, reason)
        if vosk is not None:
            phrase.vosk_conf = mean_conf(vosk.words)
        if reason in GATE_DROPS:
            assert vosk is not None
            self._gate_drop(phrase, text, vosk.text, reason)
            self._release_held(f"gate {reason}")
            return
        if reason == GATE_FOREIGN_TERMS:
            self._vosk_gate_foreign_terms += 1
        phrase.gate = reason
        self._recognized.extend(text.split())
        with self._lock:
            self._pending_recognized.append(
                {"text": text, "check": None if vosk is None else vosk.text}
            )
        phrase.src = src
        if not phrase.src:
            logger.warning("source language unknown - skipping translation")
            return
        segment, phrase.segment = phrase.segment, None  # audio no longer needed
        phrase.text = text
        if vosk is not None:
            self._vosk_checked += 1
            phrase.check = Aligned.build(
                text,
                result.segments,
                vosk.words,
                vosk.text,
                len(segment.audio) / ASR_RATE,
                segment.start_s,
            )
        elif self.settings.use.vosk != OFF:
            self._vosk_whisper_only += 1
        self._route_text(phrase, segment)

    def _is_echo(self, phrase: _Phrase, text: str, previous: List[str]) -> bool:
        """Whisper returned the previous words it was prompted with (§12
        #20): dropped - counted, logged, emitted."""
        if not is_prompt_echo(text, previous):
            return False
        self._asr_echo += 1
        logger.info(
            "phrase #{}: asr_echo -> dropped | whisper {!r} repeats the prompt's"
            " previous words",
            phrase.seq,
            text,
        )
        self._emit({"status": "asr_echo", "original_text": text})
        return True

    # -- Vosk cross-check (ASR stage) ----------------------------------------

    def _call_vosk(self, audio: np.ndarray, language: str) -> VoskResult:
        return self._pools.vosk.call(lambda c: c.transcribe(audio, language=language))

    def _vosk_submit(self, audio: np.ndarray, language: str) -> Optional[Future]:
        if self._pools.vosk is None:
            return None
        return self._vosk_exec.submit(self._call_vosk, audio, language)

    def _vosk_result(
        self,
        future: Optional[Future],
        audio: np.ndarray,
        used: Optional[str],
        src: str,
    ) -> Optional[VoskResult]:
        """Vosk's transcript in whisper's language ``src``, or None (whisper
        only: switched off, or unavailable - the state says why)."""
        if self._pools.vosk is None:
            return None
        try:
            if future is not None and used == src:
                result = future.result()
            else:
                if future is not None:
                    future.cancel()  # wrong language; a running call is ignored
                self._vosk_after_whisper += 1
                logger.info(
                    "vosk: called after whisper in {} ({})",
                    src,
                    "language changed" if used else "no language known yet",
                )
                result = self._call_vosk(audio, src)
        except ServiceError as e:
            if e.kind == "wrong_language":
                # rt-vosk holds one model; switching it is the user's
                # explicit action (settings, Vosk tab), never automatic
                self._set_vosk_state("unavailable", f"немає моделі для «{src}»: {e}")
            else:
                self._set_vosk_state("unavailable", f"{e.service}: {e.kind}: {e}")
            return None
        self._set_vosk_state("on", None)
        return result

    def _set_vosk_state(self, mode: str, error: Optional[str]) -> None:
        old = self._vosk_state
        self._vosk_state = {"mode": mode, "error": error}
        if old["mode"] == mode:
            if error:
                logger.info("vosk still unavailable ({}): whisper-only phrase", error)
            return
        if mode == "on":
            logger.info("vosk cross-check back on")
        else:
            logger.warning(
                "vosk cross-check {} ({}): whisper-only translation", mode, error
            )
        self._emit({"status": "vosk_state", "vosk": dict(self._vosk_state)})

    def _gate_drop(self, phrase: _Phrase, text: str, check: str, reason: str) -> None:
        """Whisper clearly hallucinated the segment (``gate_reason``): it is
        not translated - counted, logged, emitted."""
        self._vosk_gate_dropped += 1
        if reason == GATE_DISAGREE:
            self._vosk_gate_disagree += 1
        logger.info(
            "phrase #{}: gate {} -> dropped | whisper {!r} | vosk {!r} (conf {},"
            " agree {})",
            phrase.seq,
            reason,
            text,
            check,
            _fmt(phrase.vosk_conf),
            _fmt(agreement(text, check, phrase.src or self._last_language)),
        )
        self._emit(
            {
                "status": "vosk_gate",
                "reason": reason,
                "original_text": text,
                "check_text": check,
            }
        )

    def _to_llm(self, phrase: _Phrase) -> None:
        """Hand a phrase to the LLM stage. No gate here: the segment-level
        gate already vetoed hallucinated segments, and a piece's Vosk span
        (time-interpolated) may be empty although its words were spoken -
        dropping it deleted real words (live test 2026-10-31 §10)."""
        self._put(self._llm_q, phrase, "llm")

    # -- per-phrase log line and the session recording -----------------------

    def _record_segment(
        self,
        phrase: _Phrase,
        language: Optional[str],
        text: str,
        vosk: Optional[VoskResult],
        gate: Optional[str],
    ) -> None:
        rec = self._recorder
        if rec is None or not rec.active or phrase.segment is None:
            return
        segment = phrase.segment
        rec.segment(
            phrase.seq,
            segment.audio,
            ASR_RATE,
            {
                "start_s": round(segment.start_s, 3),
                "end_s": round(segment.end_s, 3),
                "language": language,
                "whisper": text,
                "vosk": None if vosk is None else vosk.text,
                "vosk_words": (
                    None
                    if vosk is None
                    else [
                        {"word": w.word, "conf": w.conf, "start": w.start, "end": w.end}
                        for w in vosk.words
                    ]
                ),
                "vosk_conf": None if vosk is None else mean_conf(vosk.words),
                "agreement": (
                    None if vosk is None else agreement(text, vosk.text, language)
                ),
                "gate": "no_vosk" if vosk is None and gate is None else gate,
            },
        )

    def _log_phrase(
        self, phrase: _Phrase, translation: str, rejected: Optional[str]
    ) -> None:
        """One INFO line per translated phrase: what whisper and Vosk heard,
        the gate, the LLM input mode and the result."""
        mode = "whisper-only" if phrase.check is None else "two-text"
        hint = None if phrase.check is None else phrase.check.check_text
        logger.info(
            "phrase #{}: gate pass ({}) | whisper {!r} | vosk {!r} (conf {},"
            " agree {}) | llm {} | {} {!r}",
            phrase.seq,
            phrase.gate or "no_vosk",
            phrase.text,
            hint,
            _fmt(phrase.vosk_conf),
            _fmt(None if hint is None else agreement(phrase.text, hint, phrase.src)),
            mode,
            f"rejected ({rejected}):" if rejected else "->",
            translation,
        )
        rec = self._recorder
        if rec is not None and rec.active:
            rec.translation(
                phrase.seq,
                {
                    "mode": mode,
                    "gate": phrase.gate,
                    "text": phrase.text,
                    "hint": hint,
                    "translation": translation,
                    "rejected": rejected,
                },
            )

    def _start_recording(self) -> None:
        if self._recorder is not None and self._recorder.active:
            return
        self._recorder = self._recorder_factory()
        path = self._recorder.start()
        self._emit(
            {
                "status": "session_record",
                "path": None if path is None else str(path),
                "error": self._recorder.error,
            }
        )

    def _stop_recording(self) -> None:
        if self._recorder is not None and self._recorder.active:
            self._recorder.close()
            self._emit({"status": "session_record", "path": None, "error": None})

    # -- incomplete sentences (ASR stage) -----------------------------------

    def _hold_wait_s(self) -> float:
        end_silence = self.settings.segmentation.translate.end_silence_ms
        return (end_silence or 0) / 1000.0

    def _hold_cap_s(self) -> float:
        """Longest wait for a continuation even while speech goes on: the
        pause that ends a phrase plus the length after which the segmenter
        cuts at the first short pause - a continuation spoken right after
        the held text is recognized by then (§12: a tail waited 20 s while
        dropped segments kept the VAD "in speech")."""
        cfg = self.settings.segmentation.translate
        return ((cfg.end_silence_ms or 0) + cfg.soft_max_ms) / 1000.0

    def _route_text(self, phrase: _Phrase, segment: Segment) -> None:
        """Complete sentences go on to the LLM, an incomplete tail is held
        and prepended to the next recognized segment."""
        start_s = segment.start_s
        merged = False
        held, self._held = self._held, None
        if held is not None:
            if held.src != phrase.src:
                self._held = held
                self._flush_held("language changed")
            elif (held.check is None) != (phrase.check is None):
                self._held = held
                self._flush_held("vosk cross-check mode changed")
            else:
                phrase.text = f"{held.text} {phrase.text}"
                if held.check is not None and phrase.check is not None:
                    phrase.check = held.check + phrase.check
                    confs = [
                        c for c in (held.vosk_conf, phrase.vosk_conf) if c is not None
                    ]
                    phrase.vosk_conf = sum(confs) / len(confs) if confs else None
                start_s = self._held_start_s
                if held.gate and held.gate != phrase.gate:
                    phrase.gate = "+".join(g for g in (held.gate, phrase.gate) if g)
                merged = True
                self._asr_merged += 1
                logger.info(
                    "phrase #{}: held text of #{} merged: {!r}",
                    phrase.seq,
                    held.seq,
                    phrase.text,
                )
        ready, rest = split_complete(phrase.text)
        # one segment's tail is always held; a tail that already was held
        # and still has no sentence end after ``hard_max_ms`` is sent
        max_s = self.settings.segmentation.translate.hard_max_ms / 1000.0
        if merged and rest and not ready and segment.end_s - start_s >= max_s:
            logger.info(
                "phrase #{}: incomplete text spans {:.1f} s >= {:.1f} s - sent"
                " without waiting: {!r}",
                phrase.seq,
                segment.end_s - start_s,
                max_s,
                rest,
            )
            ready, rest = phrase.text, ""
        head = tail = None
        if phrase.check is not None:
            head, tail = phrase.check.cut(len(ready.split()))
        if rest:
            self._held = _Phrase(
                phrase.seq,
                phrase.t_end,
                text=rest,
                src=phrase.src,
                check=tail,
                vosk_conf=phrase.vosk_conf,
                gate=phrase.gate,
            )
            self._held.asr_ms = phrase.asr_ms
            self._held_since = time.monotonic()
            self._held_start_s = start_s if not ready else segment.start_s
            logger.info(
                "phrase #{}: holding incomplete text for the next segment: {!r}",
                phrase.seq,
                rest,
            )
        if ready:
            phrase.text, phrase.check = ready, head
            self._to_llm(phrase)

    def _speech_in_progress(self) -> bool:
        with self._vad_lock:
            return self._vad is not None and self._vad.in_speech()

    def _release_held_if_due(self) -> None:
        if self._held is None:
            return
        waited = time.monotonic() - self._held_since
        if waited < self._hold_wait_s():
            return
        if waited >= self._hold_cap_s():
            self._flush_held(f"waited {waited:.1f} s >= {self._hold_cap_s():.1f} s")
            return
        if self._speech_in_progress():  # the continuation is being spoken
            return
        self._flush_held("no continuation")

    def _release_held(self, why: str) -> None:
        """The segment after a held tail was dropped: its speech is not a
        continuation, so the tail goes on now (§12: dropped #19/#20 kept a
        tail waiting)."""
        if self._held is not None:
            self._flush_held(f"next segment dropped: {why}")

    def _flush_held(self, why: str) -> None:
        """Send the held incomplete text alone - visibly."""
        held, self._held = self._held, None
        if held is None:
            return
        self._asr_held_flushed += 1
        logger.info(
            "phrase #{}: incomplete text sent alone ({}): {!r}",
            held.seq,
            why,
            held.text,
        )
        self._to_llm(held)

    # -- LLM warm-up ---------------------------------------------------------

    def _warm_up(self, reason: str) -> None:
        """One tiny explicit request so the worker has the model loaded
        before the first phrase (live test 2026-10-31 §6: 11 s cold start)."""
        llm = self._llm
        if llm is None:
            return
        t0 = time.monotonic()
        info: Dict[str, Any] = {"reason": reason, "url": None, "error": None}
        try:
            info["url"] = llm.base_url
            llm.warm_up()
        except Exception as e:  # reported below; never kills the LLM stage
            info["error"] = f"{type(e).__name__}: {e}"
        info["ms"] = _ms(time.monotonic() - t0)
        self._llm_warmup = info
        if info["error"]:
            logger.warning(
                "llm warm-up ({}) on {} failed after {} ms: {}",
                reason,
                info["url"],
                info["ms"],
                info["error"],
            )
        else:
            logger.info(
                "llm warm-up ({}) on {}: {} ms", reason, info["url"], info["ms"]
            )
        self._emit({"status": "llm_warmup", "warmup": dict(info)})

    def _warm_up_async(self, url: str) -> None:
        """WorkerSessions switched to another worker: warm it up off the
        session thread (it holds the session lock)."""
        if not self.is_running or self._mode != "translate":
            return
        threading.Thread(
            target=self._warm_up,
            args=(f"switched to {url}",),
            daemon=True,
            name="rt-llm-warmup",
        ).start()

    def _warm_up_tts(self) -> None:
        """One short synthesis at pipeline start (TTS stage thread, before
        the first sentence): Kokoro's first inference is slow (6.3 s live,
        2026-10-31 §10). Logged and in stats; a failure never stops TTS."""
        t0 = time.monotonic()
        info: Dict[str, Any] = {"url": None, "error": None}
        tts = self.settings.tts
        pool = self._pools.tts
        assert pool is not None  # checked by the caller
        try:
            pool.call(lambda c: c.synthesize(TTS_WARMUP_TEXT, tts.voice, tts.speed))
            info["url"] = (pool.active() or {}).get("url")
        except Exception as e:  # reported below
            info["error"] = f"{type(e).__name__}: {e}"
        info["ms"] = _ms(time.monotonic() - t0)
        self._tts_warmup = info
        if info["error"]:
            logger.warning(
                "tts warm-up failed after {} ms: {}", info["ms"], info["error"]
            )
        else:
            logger.info("tts warm-up on {}: {} ms", info["url"], info["ms"])
        self._emit({"status": "tts_warmup", "warmup": dict(info)})

    def _translate(self, phrase: _Phrase) -> None:
        """LLM stage: stream the translation, each sentence on to TTS.

        Every sentence passes ``reject_reason`` (length) on the cumulative
        output before TTS gets it; a short source (<= SHORT_SOURCE_WORDS) is
        buffered whole first - those are the fragments small models answer
        instead of translating, and buffering them costs almost no latency.
        """
        t0 = self._clock()
        self._llm_requests += 1
        sentences: List[str] = []
        held: List[_Sentence] = []
        buffer_all = len(phrase.text.split()) <= SHORT_SOURCE_WORDS
        reason: Optional[str] = None
        stream = self._llm.translate_stream(
            phrase.text,
            phrase.src,
            context=list(self._translation_context),
            check=phrase.check,
        )
        try:
            for sentence in stream:
                now = self._clock()
                if phrase.llm_first_ms is None:
                    phrase.llm_first_ms = _ms(now - t0)
                sentences.append(sentence)
                reason = reject_reason(phrase.text, " ".join(sentences))
                if reason:
                    break
                item = _Sentence(phrase, sentence, now)
                if buffer_all:
                    held.append(item)
                else:
                    self._queue_sentence(item)
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
            close = getattr(stream, "close", None)
            if close:  # release the HTTP stream when we stopped early
                close()
            self._llm_time += self._clock() - t0
        if reason:
            self._log_phrase(phrase, " ".join(sentences), reason)
            self._reject(phrase, reason, " ".join(sentences))
            return
        for item in held:
            self._queue_sentence(item)
        phrase.llm_ms = _ms(self._clock() - t0)
        translation = " ".join(sentences)
        self._log_phrase(phrase, translation, None)
        if phrase.check is None:
            self._translation_context.append((phrase.text, translation))
        else:
            self._translation_context.append(
                (phrase.text, translation, phrase.check.check_text)
            )
        with self._lock:
            self._pending_translated.append(translation)
        self._emit(
            {
                "status": "translation_complete",
                "original_text": phrase.text,
                "translated_text": translation,
            }
        )

    def _queue_sentence(self, item: _Sentence) -> None:
        item.phrase.pieces += 1
        self._put(self._tts_q, item, "tts")

    def _reject(self, phrase: _Phrase, reason: str, output: str) -> None:
        """Not a translation (chat/meta reply): nothing more goes to TTS and
        the pair stays out of the few-shot context - visibly."""
        self._llm_rejected += 1
        logger.warning(
            "LLM output rejected ({}): source {!r}, output {!r:.200}",
            reason,
            phrase.text,
            output,
        )
        self._emit(
            {
                "status": "translation_rejected",
                "reason": reason,
                "original_text": phrase.text,
                "output": output[:200],
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
        vosk = self._pools.vosk
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
            "llm_rejected": self._llm_rejected,
            "llm_avg_latency_s": (
                round(self._llm_time / self._llm_requests, 3)
                if self._llm_requests
                else 0.0
            ),
            "discovery": self._directory.source,
            "active": {
                "asr": self._pools.asr.active(),
                "vosk": vosk.active() if vosk is not None else None,
                "tts": tts.active() if tts is not None else None,
            },
            # TASK-SYS-12: on | off (settings) | unavailable (+ why)
            "vosk": dict(self._vosk_state),
            "vosk_checked": self._vosk_checked,
            "vosk_gate_dropped": self._vosk_gate_dropped,
            "vosk_gate_disagree": self._vosk_gate_disagree,
            "vosk_gate_foreign_terms": self._vosk_gate_foreign_terms,
            "vosk_whisper_only": self._vosk_whisper_only,
            "vosk_after_whisper": self._vosk_after_whisper,
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
            "asr_no_words": self._asr_no_words,
            "asr_merged": self._asr_merged,
            "asr_held_flushed": self._asr_held_flushed,
            "asr_echo": self._asr_echo,
            "playback_short": self._playback_short,
            "llm_warmup": dict(self._llm_warmup) if self._llm_warmup else None,
            "tts_warmup": dict(self._tts_warmup) if self._tts_warmup else None,
            "session_record": (
                None
                if self._recorder is None
                else {
                    "on": self._recorder.active,
                    "path": str(self._recorder.path) if self._recorder.path else None,
                    "error": self._recorder.error,
                }
            ),
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
