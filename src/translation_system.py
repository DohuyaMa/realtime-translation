"""Edge coordinator: mic -> rt-asr / rt-vosk streams -> units -> LLM -> rt-tts
-> playback (spec 2026-10-31 «streaming ASR + simultaneous translation»).

Capture (48 kHz) is resampled to 16 kHz PCM16 and fed to two WS streams
(``clients.stream.StreamClient``): rt-asr (whisper, LocalAgreement-2 - its
finals are committed words that are never revised) and, in translate mode,
rt-vosk (the cross-check); both send word times on one timeline.

Simultaneous translation (``core.simul``): whisper's committed words go into
an append-only ``SourceLedger``; ``next_unit`` cuts the next unit at a
sentence end / an endpoint (silence) / >= 12 untranslated words at a clause
mark / hard 20 words (owner decision 1). Units never overlap: nothing is
translated or spoken twice, a spoken translation is never corrected
(decision 3). Whisper's uncommitted tail (and committed words not yet in a
unit) is shown grey in the UI (``get_stats()["source_tail"]``), never
spoken (decision 2).

Vosk gate per unit (TASK-SYS-12, ``core.crosscheck.gate_reason``): the unit
waits until Vosk has covered its end t1 - at most ``VOSK_WAIT_S`` - then its
HINT is the Vosk words of [t0 - 0.25, t1 + 0.25] (``core.simul.VoskTrack``:
covered = the end of Vosk's latest word). Only clear hallucination
is dropped (``vosk_gate_dropped`` / ``_disagree``, a dropped unit only moves
the ledger on); Vosk late, off or unavailable -> whisper-only, counted and
logged. Whisper's echo of its prompt is dropped by rt-asr itself.

LLM request per unit: the previous source -> target pairs as context, then
``TEXT:`` the unit, ``HINT:`` its Vosk words (two-text mode) and ``NEXT (do
not translate):`` the committed words after it + whisper's partial, for
understanding only (``core.output_guard`` rejects NEXT leakage).

Staged pipeline (owner 2026-10-31): the unit stage, LLM, TTS and playback
are worker threads with bounded FIFO queues - the LLM translates unit n+1
while TTS synthesizes n and n-1 plays. A full downstream queue blocks the
stage (backpressure, logged + ``backpressure`` status event). The LLM output
is streamed: each finished sentence goes to TTS at once. Each unit is logged
in one INFO line (whisper, Vosk + mean conf + agreement, gate, LLM mode,
NEXT, translation) and its timing (speech end -> commit -> LLM -> TTS ->
playback) in another; ``get_stats()["latency"]`` keeps p50/p90. The opt-in
session recording (``core.session_record``) keeps each unit's audio.

Warm-up (owner 2026-10-31, spec §3a): Start warms every service in parallel
- it opens both streams (the handshake loads the model, an unloaded one gets
``POST /model/load`` first; the server decodes 1 s of silence after
``ready``), the LLM (one 1-token completion) and rt-tts (load if needed +
one short synthesis). Capture starts at once; audio waits in the stream's
ring buffer (<= 30 s) until the stream is ready. Each warm-up is logged with
its ms, the UI shows «прогрів…» with the services still warming
(``warmup_state`` events) and one INFO line gives Start -> all ready.

Transcribe mode: only the rt-asr stream; every unit is one recognized line,
no worker session is opened.
"""

from __future__ import annotations

import bisect
import queue
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, List, Optional, Sequence

import numpy as np
from loguru import logger

from .audio.resample import Downsampler, rms
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
from .clients.model_status import ensure_model_ready
from .clients.stream import StreamClient
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
from .core.prompt import render_asr_prompt
from .core.runtime import KINDS, OFF, EdgeSettings, LlmSettings
from .core.session_record import SessionRecorder
from .core.simul import SourceLedger, Unit, VoskTrack, Word, parse_words, words_text

CAPTURE_RATE = 48000
BLOCKSIZE = 1024
ASR_RATE = 16000
STAGE_QUEUE_MAX = 4
PLAYBACK_QUEUE_MAX = 8
# sources of at most this many words are translated whole before TTS gets
# anything (see _translate)
SHORT_SOURCE_WORDS = 3
# an unknown capture source makes parec exit within this time
CAPTURE_START_CHECK_S = 0.2
# TTS warm-up text (first Kokoro synthesis took 6.3 s live, 2026-10-31 §10)
TTS_WARMUP_TEXT = "Hello."
# a unit waits at most this long for Vosk to cover its end (spec §4)
VOSK_WAIT_S = 0.3
# the unit stage re-checks waiting units this often
UNIT_POLL_S = 0.02
# playback that returns this much earlier than its audio lasts is reported
PLAYBACK_SHORT_S = 0.3
# (stream seconds fed, clock) pairs kept to map word times to wall time
FED_TIMES_MAX = 4000
# latency samples kept for get_stats()["latency"] p50/p90
LATENCY_KEEP = 500

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
class _Unit:
    """One translation unit travelling through the stages, with its timing."""

    seq: int
    source: Unit
    t_end: float  # clock() when the audio of the unit's last word was fed
    t_cut: float  # clock() when the unit was cut (its last word committed)
    text: str = ""
    src: Optional[str] = None
    next_text: str = ""  # NEXT: speech after the unit (understanding only)
    # Vosk words of the unit's span aligned with it; None = whisper-only
    check: Optional[Aligned] = None
    vosk_conf: Optional[float] = None
    gate: Optional[str] = None  # gate_reason of a passed unit
    commit_ms: Optional[float] = None
    llm_first_ms: Optional[float] = None
    llm_ms: Optional[float] = None
    tts_first_ms: Optional[float] = None
    # speech end -> first translated audio ready (before the playback queue)
    ready_ms: Optional[float] = None
    e2e_ms: Optional[float] = None
    pieces: int = 0  # sentences sent to TTS
    played: bool = False
    played_pieces: int = 0


@dataclass
class _Sentence:
    unit: _Unit
    text: str
    t_ready: float  # when the LLM produced it


def _fmt(value: Optional[float]) -> str:
    return "-" if value is None else f"{value:.2f}"


def _ms(seconds: float) -> float:
    return round(seconds * 1000.0, 1)


def _pct(values: Sequence[float], q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(q * len(ordered)))], 1)


def default_llm_target(
    settings: EdgeSettings, directory: Directory
) -> Optional[LlmTarget]:
    workers = endpoints("llm", settings, directory)
    if not workers:
        return None
    return WorkerSessions(workers, settings.llm.session_ttl_s)


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
        llm_factory: Callable[[LlmTarget, LlmSettings], Any] = LlmClient,
        capture_factory: Optional[Callable[[Callable[[np.ndarray], None]], Any]] = None,
        playback_sink: Optional[Callable[[np.ndarray, int], None]] = None,
        clock: Callable[[], float] = time.monotonic,
        recorder_factory: Callable[[], SessionRecorder] = SessionRecorder,
        stream_factory: Callable[..., Any] = StreamClient,
    ) -> None:
        self.settings = settings
        self._recorder_factory = recorder_factory
        # «Записувати сесію для аналізу» (settings.record_session)
        self._recorder: Optional[SessionRecorder] = None
        self._stream_factory = stream_factory
        # rt-asr / rt-vosk WS streams of the running pipeline (kind -> client)
        self._streams: Dict[str, Any] = {}
        self._downsampler = Downsampler(CAPTURE_RATE)
        self._directory_factory = directory_factory
        self._pools_factory = pools_factory
        self._llm_target_factory = llm_target_factory
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
        self._mode = settings.effective_mode()
        self._session_held = False
        self._stream: Any = None
        self._clock = clock
        # stream events (kind, msg) -> the unit stage
        self._events: queue.Queue = queue.Queue()
        self._llm_q: queue.Queue = queue.Queue(maxsize=STAGE_QUEUE_MAX)
        self._tts_q: queue.Queue = queue.Queue(maxsize=STAGE_QUEUE_MAX)
        self._playback_queue: queue.Queue = queue.Queue(maxsize=PLAYBACK_QUEUE_MAX)
        # unit stage state (its thread only)
        self._ledger = SourceLedger()
        self._vosk = VoskTrack()
        self._waiting: Deque[_Unit] = deque()  # cut, waiting for Vosk
        self._draining = False  # stop: no more waiting for Vosk
        self._detected_language: Optional[str] = None
        self._tail = ""  # ledger.tail() for the UI (grey)
        # stream seconds fed -> clock(): word times to wall time
        self._fed_lock = threading.Lock()
        self._fed_s: Deque[float] = deque(maxlen=FED_TIMES_MAX)
        self._fed_at: Deque[float] = deque(maxlen=FED_TIMES_MAX)
        self._fed_samples = 0
        self._seq = 0
        self._timing: Dict[str, Any] = {}
        self._latency: Dict[str, Deque[float]] = {
            k: deque(maxlen=LATENCY_KEEP)
            for k in (
                "commit_ms",
                "llm_first_ms",
                "tts_first_ms",
                "ready_ms",
                "e2e_ms",
            )
        }
        self._backpressure: Dict[str, int] = {"llm": 0, "tts": 0, "playback": 0}
        self._playback_short = 0
        self._asr_no_words = 0
        # Vosk cross-check (TASK-SYS-12)
        self._vosk_state: Dict[str, Any] = {"mode": "off", "error": None}
        self._vosk_checked = 0
        self._vosk_gate_dropped = 0
        self._vosk_gate_disagree = 0  # of those: whisper and Vosk disagree
        self._vosk_gate_foreign_terms = 0  # passed: only foreign-script words
        self._vosk_whisper_only = 0
        self._vosk_late = 0  # whisper-only: Vosk did not cover t1 in time
        # pipeline-start warm-up: kind -> {url, ms, error, loaded}; kinds
        # still warming; time.monotonic() of start()
        self._warmup: Dict[str, Dict[str, Any]] = {}
        self._warmup_pending: set = set()
        self._start_t = 0.0
        self._audio_q: queue.Queue = queue.Queue()
        # periodic Django discovery runs unless the kill switch paused it
        self._background = True
        self._capturing = False
        self._threads: Dict[str, threading.Thread] = {}

        self._translation_context: deque = deque(maxlen=settings.llm.context_pairs)
        # recognized units for the UI: {"text": whisper, "check": Vosk's
        # text of the same span, None = no Vosk check for this unit}
        self._pending_recognized: List[Dict[str, Optional[str]]] = []
        self._pending_translated: List[str] = []
        self._audio_level_input = 0.0
        self._units_processed = 0
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
        self._start_t = time.monotonic()
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

        self._audio_q = queue.Queue()
        self._events = queue.Queue()
        self._llm_q = queue.Queue(maxsize=STAGE_QUEUE_MAX)
        self._tts_q = queue.Queue(maxsize=STAGE_QUEUE_MAX)
        self._playback_queue = queue.Queue(maxsize=PLAYBACK_QUEUE_MAX)
        self._ledger = SourceLedger()
        self._vosk = VoskTrack()
        self._waiting = deque()
        self._draining = False
        self._detected_language = None
        self._tail = ""
        with self._fed_lock:
            self._fed_s.clear()
            self._fed_at.clear()
            self._fed_samples = 0
        self._translation_context.clear()
        self._vosk_state = self._initial_vosk_state()
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
        self._begin_warmup()
        self._open_streams()
        self.is_running = True
        self._capturing = True
        self._threads = {
            name: threading.Thread(target=fn, daemon=True, name=f"rt-{name}")
            for name, fn in (
                ("units", self._unit_worker),
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
        # 1. stop capture and feed what is buffered to the streams
        self._capturing = False
        capture = self._threads.get("capture")
        if capture:
            capture.join(timeout=5.0)
        # 2. streams: the rest of the audio, stop -> last finals -> done
        self._stop_streams()
        # 3. _STOP travels units -> llm -> tts -> playback behind the queued
        #    work: the ledger is flushed, every unit translated and played
        self._events.put(_STOP)
        for name in ("units", "llm", "tts", "playback"):
            t = self._threads.get(name)
            if t:
                t.join(timeout=60.0)
                if t.is_alive():
                    logger.warning("{} stage still busy after 60 s at stop", name)
        self.is_running = False
        self._threads = {}
        self._streams = {}
        self._stop_recording()
        # 4. close the worker session
        self._close_session()
        logger.info(
            "translation system stopped: {} units, {} errors",
            self._units_processed,
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

    @staticmethod
    def _needs_restart(old: EdgeSettings, new: EdgeSettings) -> bool:
        if old.effective_mode() != new.effective_mode():
            return True
        # the streams get the language and the whisper prompt at their start
        for name in (
            "django_urls",
            "django_token_file",
            "local",
            "local_token_file",
            "allowed_services",
            "use",
            "timeouts",
            "source_language",
            "asr",
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

    # -- capture -> streams ---------------------------------------------------

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
        """48 kHz block -> 16 kHz PCM16 into every stream; the signal level
        for the UI is its RMS."""
        self._audio_level_input = min(rms(block) * 5.0, 1.0)
        pcm = self._downsampler(block)
        for stream in list(self._streams.values()):
            stream.feed(pcm)
        with self._fed_lock:
            self._fed_samples += len(pcm)
            self._fed_s.append(self._fed_samples / ASR_RATE)
            self._fed_at.append(self._clock())

    def _wall(self, t: float) -> float:
        """clock() when stream second ``t`` was fed (now if not yet)."""
        with self._fed_lock:
            i = bisect.bisect_left(self._fed_s, t)
            if i < len(self._fed_at):
                return self._fed_at[i]
        return self._clock()

    def _language(self) -> Optional[str]:
        lang = self.settings.source_language
        return None if lang == "auto" else lang

    def _stream_prompt(self) -> Optional[str]:
        """Whisper prompt of the stream: the user's template; rt-asr adds
        the committed words itself ({{previous_text}} is left empty)."""
        return render_asr_prompt(self.settings.asr.prompt, [], 0, self._language())

    def _open_streams(self) -> None:
        """Open the WS streams (their warm-up); audio fed before ``ready``
        waits in each stream's ring buffer."""
        self._downsampler = Downsampler(CAPTURE_RATE)
        lang = self._language()
        kinds: List[tuple] = [("asr", self._pools.asr, self._stream_prompt())]
        if self._mode == "translate" and self._vosk_state["mode"] == "on":
            kinds.append(("vosk", self._pools.vosk, None))
        streams = {
            kind: self._stream_factory(
                kind, pool, language=lang, prompt=prompt, on_event=self._on_stream_event
            )
            for kind, pool, prompt in kinds
        }
        self._streams = streams
        for stream in streams.values():
            stream.start()

    def _stop_streams(self) -> None:
        """Stop every stream at once (each waits for its last finals)."""
        threads = [
            threading.Thread(target=s.stop, daemon=True, name=f"rt-{k}-stop")
            for k, s in self._streams.items()
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    def _on_stream_event(self, kind: str, msg: Dict[str, Any]) -> None:
        """Stream client thread: ``ready`` / ``failed`` end the stream's
        warm-up; everything goes on to the unit stage in order."""
        t = msg.get("type")
        if t == "ready" and not msg.get("reconnect"):
            self._warmed(
                kind,
                {
                    "url": msg.get("url"),
                    "ms": msg.get("ms"),
                    "loaded": msg.get("loaded", False),
                    "error": None,
                },
            )
        elif t == "failed" and kind in self._warmup_pending:
            self._warmed(
                kind,
                {
                    "url": None,
                    "ms": _ms(time.monotonic() - self._start_t),
                    "loaded": False,
                    "error": msg.get("error"),
                },
            )
        self._events.put((kind, msg))

    # -- unit stage -------------------------------------------------------------

    def _unit_worker(self) -> None:
        while True:
            try:
                item = self._events.get(timeout=UNIT_POLL_S)
            except queue.Empty:
                item = None
            try:
                if item is _STOP:
                    self._draining = True
                    unit = self._ledger.flush()
                    if unit is not None:
                        self._waiting.append(self._new_unit(unit))
                    self._dispatch()
                    self._llm_q.put(_STOP)
                    return
                if item is not None:
                    self._apply(*item)
                for unit in self._ledger.units():
                    self._waiting.append(self._new_unit(unit))
                self._dispatch()
                self._tail = self._ledger.tail()
            except Exception as e:
                logger.exception("unit stage error: {}", e)
                self._pipeline_errors += 1
                self._fail("pipeline", type(e).__name__)

    def _apply(self, kind: str, msg: Dict[str, Any]) -> None:
        t = msg.get("type")
        if kind == "vosk":
            self._apply_vosk(t, msg)
            return
        if t == "final":
            words = parse_words(msg.get("words"))
            if msg.get("language") and self._detected_language is None:
                self._detected_language = str(msg["language"])
                self._check_vosk_language()
            self._ledger.commit(words, eos=bool(msg.get("eos")))
        elif t == "partial":
            self._ledger.partial = str(msg.get("text") or "")
        elif t == "endpoint":
            self._ledger.endpoint()
        elif t in ("down", "failed"):
            self._pipeline_errors += 1
            self.last_error = f"asr: {msg.get('error')}"
            self._emit(
                {
                    "status": "error",
                    "service": "asr",
                    "kind": "unreachable",
                    "error": self.last_error,
                }
            )

    def _apply_vosk(self, t: Optional[str], msg: Dict[str, Any]) -> None:
        if t in ("final", "partial", "endpoint"):
            self._vosk.on_message(msg)
            if t == "final":
                self._check_vosk_language()
        elif t == "ready":
            if self._vosk_state["mode"] == "unavailable":
                self._set_vosk_state("on", None)
        elif t in ("down", "failed"):
            error = str(msg.get("error") or "")
            if "model_language_mismatch" in error:
                lang = self._language() or self._detected_language or "?"
                self._vosk_off(f"немає моделі для «{lang}»: {error}")
            else:
                self._set_vosk_state("unavailable", f"vosk: {error}")

    def _check_vosk_language(self) -> None:
        """Auto source language: whisper's language is fixed on the first
        words; a Vosk model of another language cannot check it."""
        whisper, vosk = self._detected_language, self._vosk.language
        if whisper and vosk and whisper != vosk and "vosk" in self._streams:
            self._vosk_off(f"немає моделі для «{whisper}» (модель Vosk: «{vosk}»)")

    def _vosk_off(self, why: str) -> None:
        """rt-vosk holds one model; switching it is the user's explicit
        action (settings, Vosk tab), never automatic: the stream closes and
        the session goes on whisper-only."""
        stream = self._streams.pop("vosk", None)
        if stream is not None:
            stream.close()
        self._set_vosk_state("unavailable", why)

    def _new_unit(self, unit: Unit) -> _Unit:
        self._seq += 1
        now = self._clock()
        t_end = self._wall(unit.t1)
        u = _Unit(self._seq, unit, t_end, now)
        u.commit_ms = _ms(now - t_end)
        self._latency["commit_ms"].append(u.commit_ms)
        return u

    def _vosk_active(self) -> bool:
        return "vosk" in self._streams and self._vosk_state["mode"] == "on"

    def _dispatch(self) -> None:
        """Units in order: each waits <= VOSK_WAIT_S for Vosk to cover its
        end (not while stopping), then goes through the gate."""
        while self._waiting:
            unit = self._waiting[0]
            vosk = self._vosk_active() and self._mode == "translate"
            if (
                vosk
                and not self._draining
                and not self._vosk.covers(unit.source.t1)
                and self._clock() - unit.t_cut < VOSK_WAIT_S
            ):
                return
            self._waiting.popleft()
            self._units_processed += 1
            if self._mode == "translate":
                self._gate(unit, vosk)
            else:
                self._transcribed(unit)

    def _transcribed(self, unit: _Unit) -> None:
        text = unit.source.text
        if not self._has_words(text):
            return
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

    def _gate(self, unit: _Unit, vosk: bool) -> None:
        """The Vosk gate of one unit, then the unit on to the LLM stage."""
        src = self._language() or self._detected_language
        text = unit.source.text
        if not self._has_words(text):
            return
        hint: Optional[List[Word]] = None
        partial = self._vosk.hint(unit.source.t0, unit.source.t1) if vosk else []
        if vosk and (self._vosk.covers(unit.source.t1) or partial):
            # Kaldi's partial lags ~1 s: Vosk words already in the span are
            # used as the HINT even when its end is not covered yet
            hint = partial
        elif vosk and not self._draining:
            self._vosk_late += 1
            logger.info(
                "unit #{}: vosk covered {:.2f} s < end {:.2f} s after {} ms"
                " - whisper-only",
                unit.seq,
                self._vosk.covered,
                unit.source.t1,
                _ms(VOSK_WAIT_S),
            )
        elif vosk:  # stopping: Vosk sent all it had
            hint = self._vosk.hint(unit.source.t0, unit.source.t1)
        elif self.settings.use.vosk != OFF:
            self._vosk_whisper_only += 1
        self._vosk.forget_before(unit.source.t0)
        hint_text = None if hint is None else words_text(hint)
        reason = None if hint_text is None else gate_reason(text, hint_text, src)
        if hint is not None:
            unit.vosk_conf = mean_conf(hint)
        unit.src = src
        self._record_unit(unit, src, text, hint, reason)
        if reason in GATE_DROPS:
            assert hint_text is not None
            self._gate_drop(unit, text, hint_text, reason)
            return
        if reason == GATE_FOREIGN_TERMS:
            self._vosk_gate_foreign_terms += 1
        unit.gate = reason
        unit.text = text
        with self._lock:
            self._pending_recognized.append({"text": text, "check": hint_text})
        if not src:
            logger.warning("source language unknown - skipping translation")
            return
        if hint is not None:
            self._vosk_checked += 1
            unit.check = Aligned.from_words(unit.source.words, hint)
        unit.next_text = self._ledger.after(unit.source.end_index)
        self._put(self._llm_q, unit, "llm")

    def _set_vosk_state(self, mode: str, error: Optional[str]) -> None:
        old = self._vosk_state
        self._vosk_state = {"mode": mode, "error": error}
        if old["mode"] == mode:
            if error and error != old["error"]:
                logger.info("vosk still unavailable ({}): whisper-only", error)
            return
        if mode == "on":
            logger.info("vosk cross-check back on")
        else:
            logger.warning(
                "vosk cross-check {} ({}): whisper-only translation", mode, error
            )
        self._emit({"status": "vosk_state", "vosk": dict(self._vosk_state)})

    def _gate_drop(self, unit: _Unit, text: str, check: str, reason: str) -> None:
        """Whisper clearly hallucinated the unit (``gate_reason``): it is
        not translated - counted, logged, emitted; the ledger just goes on."""
        self._vosk_gate_dropped += 1
        if reason == GATE_DISAGREE:
            self._vosk_gate_disagree += 1
        logger.info(
            "unit #{}: gate {} -> dropped | whisper {!r} | vosk {!r} (conf {},"
            " agree {})",
            unit.seq,
            reason,
            text,
            check,
            _fmt(unit.vosk_conf),
            _fmt(agreement(text, check, unit.src)),
        )
        self._emit(
            {
                "status": "vosk_gate",
                "reason": reason,
                "original_text": text,
                "check_text": check,
            }
        )

    # -- per-unit log line and the session recording ----------------------------

    def _record_unit(
        self,
        unit: _Unit,
        language: Optional[str],
        text: str,
        hint: Optional[List[Word]],
        gate: Optional[str],
    ) -> None:
        rec = self._recorder
        if rec is None or not rec.active:
            return
        s = unit.source
        stream = self._streams.get("asr")
        audio = stream.audio(s.t0, s.t1) if stream is not None else np.zeros(0)
        hint_text = None if hint is None else words_text(hint)
        rec.segment(
            unit.seq,
            audio,
            ASR_RATE,
            {
                "start_s": round(s.t0, 3),
                "end_s": round(s.t1, 3),
                "reason": s.reason,
                "language": language,
                "whisper": text,
                "whisper_words": [
                    {"word": w.text, "start": w.start, "end": w.end, "conf": w.conf}
                    for w in s.words
                ],
                "vosk": hint_text,
                "vosk_words": (
                    None
                    if hint is None
                    else [
                        {"word": w.text, "conf": w.conf, "start": w.start, "end": w.end}
                        for w in hint
                    ]
                ),
                "vosk_conf": None if hint is None else mean_conf(hint),
                "agreement": (
                    None if hint_text is None else agreement(text, hint_text, language)
                ),
                "gate": "no_vosk" if hint is None and gate is None else gate,
            },
        )

    def _log_unit(self, unit: _Unit, translation: str, rejected: Optional[str]) -> None:
        """One INFO line per translated unit: what whisper and Vosk heard,
        the gate, the LLM input mode, NEXT and the result."""
        mode = "whisper-only" if unit.check is None else "two-text"
        hint = None if unit.check is None else unit.check.check_text
        logger.info(
            "unit #{} ({}, {:.2f}-{:.2f} s): gate pass ({}) | whisper {!r} |"
            " vosk {!r} (conf {}, agree {}) | llm {} | next {!r} | {} {!r}",
            unit.seq,
            unit.source.reason,
            unit.source.t0,
            unit.source.t1,
            unit.gate or "no_vosk",
            unit.text,
            hint,
            _fmt(unit.vosk_conf),
            _fmt(None if hint is None else agreement(unit.text, hint, unit.src)),
            mode,
            unit.next_text,
            f"rejected ({rejected}):" if rejected else "->",
            translation,
        )
        rec = self._recorder
        if rec is not None and rec.active:
            rec.translation(
                unit.seq,
                {
                    "mode": mode,
                    "gate": unit.gate,
                    "text": unit.text,
                    "hint": hint,
                    "next": unit.next_text,
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

    # -- warm-up ----------------------------------------------------------------

    def _warm_up(self, reason: str) -> None:
        """One tiny explicit request so the worker has the model loaded
        before the first unit (live test 2026-10-31 §6: 11 s cold start)."""
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
        self._warmed("llm", info)

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
        """TTS stage thread, before the first sentence: model ready (loaded
        if needed) + one short synthesis - Kokoro's first inference is slow
        (6.3 s live, 2026-10-31 §10). A failure is reported, never stops the
        stage."""
        pool = self._pools.tts
        t0 = time.monotonic()
        info: Dict[str, Any] = {"url": None, "error": None, "loaded": False}
        tts = self.settings.tts

        def go(client: Any) -> None:
            info["loaded"] = ensure_model_ready("tts", client)
            client.synthesize(TTS_WARMUP_TEXT, tts.voice, tts.speed)

        try:
            pool.call(go)
            info["url"] = (pool.active() or {}).get("url")
        except Exception as e:  # reported below
            info["error"] = f"{type(e).__name__}: {e}"
        info["ms"] = _ms(time.monotonic() - t0)
        self._warmed("tts", info)

    def _begin_warmup(self) -> None:
        """Which services warm up at this start (all in parallel: the
        streams on their own threads, LLM and TTS on their stages);
        announced as one ``warmup_state``."""
        kinds = ["asr"]
        if self._mode == "translate":
            kinds += ["llm", "tts"]
            if self._pools.vosk is not None:
                kinds.append("vosk")
        self._warmup = {}
        self._warmup_pending = set(kinds)
        logger.info("warm-up started: {}", ", ".join(kinds))
        self._emit({"status": "warmup_state", "ready": False, "pending": kinds})

    def _warmed(self, kind: str, info: Dict[str, Any]) -> None:
        """One warm-up finished: logged, emitted; the last one of a start
        logs Start -> all ready and emits ``warmup_state`` ready."""
        reason = info.get("reason", "start")
        what = f"{kind} warm-up" + (f" ({reason})" if reason != "start" else "")
        if info["error"]:
            logger.warning(
                "{} on {} failed after {} ms: {}",
                what,
                info["url"],
                info["ms"],
                info["error"],
            )
        else:
            logger.info(
                "{} on {}: {} ms{}",
                what,
                info["url"],
                info["ms"],
                " (model loaded first)" if info.get("loaded") else "",
            )
        self._emit({"status": "warmup", "kind": kind, "warmup": dict(info)})
        if reason != "start":
            return
        with self._lock:
            self._warmup[kind] = info
            was = bool(self._warmup_pending)
            self._warmup_pending.discard(kind)
            done = was and not self._warmup_pending
            pending = sorted(self._warmup_pending)
        if not done:
            if was and pending:  # «прогрів…» names what is still warming
                self._emit(
                    {"status": "warmup_state", "ready": False, "pending": pending}
                )
            return
        total = _ms(time.monotonic() - self._start_t)
        failed = sorted(k for k, i in self._warmup.items() if i["error"])
        logger.info(
            "warm-up: all ready {} ms after start ({}){}",
            total,
            ", ".join(f"{k} {i['ms']} ms" for k, i in sorted(self._warmup.items())),
            f" - failed: {', '.join(failed)}" if failed else "",
        )
        self._emit(
            {"status": "warmup_state", "ready": True, "ms": total, "failed": failed}
        )

    # -- LLM / TTS / playback stages ------------------------------------------

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
            unit, audio, sr = item
            if unit is not None and not unit.played:
                unit.played = True
                unit.e2e_ms = _ms(self._clock() - unit.t_end)
                self._record_timing(unit)
            self._play(unit, audio, sr)

    def _play(self, unit: Optional[_Unit], audio: np.ndarray, sr: int) -> None:
        """Play one piece; each piece is logged with its length, and a sink
        that returns before the audio could have played is reported (live
        test 2026-10-31 §1: a reply was heard only partly)."""
        duration = len(audio) / float(sr) if sr else 0.0
        label = "external audio"
        if unit is not None:
            unit.played_pieces += 1
            label = f"unit #{unit.seq} piece {unit.played_pieces}"
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

    def _record_timing(self, unit: _Unit) -> None:
        t = {
            "seq": unit.seq,
            "commit_ms": unit.commit_ms,
            "llm_ms": unit.llm_first_ms,
            "llm_total_ms": unit.llm_ms,
            "tts_ms": unit.tts_first_ms,
            "ready_ms": unit.ready_ms,
            "e2e_ms": unit.e2e_ms,
        }
        self._timing = t
        for key in ("llm_first_ms", "tts_first_ms", "ready_ms", "e2e_ms"):
            value = getattr(unit, key)
            if value is not None:
                self._latency[key].append(value)
        logger.info(
            "unit #{}: speech end -> commit {} ms, llm first sentence {} ms"
            " (total {}), tts first {} ms, speech end -> audio ready {} ms,"
            " speech end -> playback {} ms",
            unit.seq,
            unit.commit_ms,
            unit.llm_first_ms,
            unit.llm_ms,
            unit.tts_first_ms,
            unit.ready_ms,
            unit.e2e_ms,
        )
        self._emit({"status": "timing", "timing": t})

    def _service_error(self, e: ServiceError) -> None:
        self._pipeline_errors += 1
        logger.warning("{} (unit skipped)", e)
        self.last_error = f"{e.service}: {e.kind}"
        self._emit(
            {
                "status": "error",
                "service": e.service,
                "kind": e.kind,
                "error": self.last_error,
            }
        )

    def _translate(self, unit: _Unit) -> None:
        """LLM stage: stream the translation, each sentence on to TTS.

        Every sentence passes ``reject_reason`` (length, script, NEXT
        leakage) on the cumulative output before TTS gets it; a short source
        (<= SHORT_SOURCE_WORDS) is buffered whole first - those are the
        fragments small models answer instead of translating, and buffering
        them costs almost no latency.
        """
        t0 = self._clock()
        self._llm_requests += 1
        sentences: List[str] = []
        held: List[_Sentence] = []
        buffer_all = len(unit.text.split()) <= SHORT_SOURCE_WORDS
        reason: Optional[str] = None
        stream = self._llm.translate_stream(
            unit.text,
            unit.src,
            context=list(self._translation_context),
            check=unit.check,
            next_text=unit.next_text,
        )
        try:
            for sentence in stream:
                now = self._clock()
                if unit.llm_first_ms is None:
                    unit.llm_first_ms = _ms(now - t0)
                sentences.append(sentence)
                reason = reject_reason(unit.text, " ".join(sentences), unit.next_text)
                if reason:
                    break
                item = _Sentence(unit, sentence, now)
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
            self._log_unit(unit, " ".join(sentences), reason)
            self._reject(unit, reason, " ".join(sentences))
            return
        for item in held:
            self._queue_sentence(item)
        unit.llm_ms = _ms(self._clock() - t0)
        translation = " ".join(sentences)
        self._log_unit(unit, translation, None)
        if unit.check is None:
            self._translation_context.append((unit.text, translation))
        else:
            self._translation_context.append(
                (unit.text, translation, unit.check.check_text)
            )
        with self._lock:
            self._pending_translated.append(translation)
        self._emit(
            {
                "status": "translation_complete",
                "original_text": unit.text,
                "translated_text": translation,
            }
        )

    def _queue_sentence(self, item: _Sentence) -> None:
        item.unit.pieces += 1
        self._put(self._tts_q, item, "tts")

    def _reject(self, unit: _Unit, reason: str, output: str) -> None:
        """Not a translation (chat/meta reply, NEXT leaked): nothing more
        goes to TTS and the pair stays out of the context - visibly."""
        self._llm_rejected += 1
        logger.warning(
            "LLM output rejected ({}): source {!r}, output {!r:.200}",
            reason,
            unit.text,
            output,
        )
        self._emit(
            {
                "status": "translation_rejected",
                "reason": reason,
                "original_text": unit.text,
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
        unit = item.unit
        if unit.tts_first_ms is None:
            now = self._clock()
            unit.tts_first_ms = _ms(now - item.t_ready)
            unit.ready_ms = _ms(now - unit.t_end)
        self._put(self._playback_queue, (unit, audio, int(sr)), "playback")

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
            "detected_language": self._detected_language,
            "audio_level_input": self._audio_level_input,
            "units_processed": self._units_processed,
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
            "streams": {k: s.stats() for k, s in list(self._streams.items())},
            # whisper's words not yet in a unit + its partial: grey in the UI
            "source_tail": self._tail,
            # TASK-SYS-12: on | off (settings) | unavailable (+ why)
            "vosk": dict(self._vosk_state),
            "vosk_checked": self._vosk_checked,
            "vosk_gate_dropped": self._vosk_gate_dropped,
            "vosk_gate_disagree": self._vosk_gate_disagree,
            "vosk_gate_foreign_terms": self._vosk_gate_foreign_terms,
            "vosk_whisper_only": self._vosk_whisper_only,
            "vosk_late": self._vosk_late,
            "session": self._session_snapshot(),
            "timing": dict(self._timing),
            "latency": {
                k: {"p50": _pct(list(v), 0.5), "p90": _pct(list(v), 0.9), "n": len(v)}
                for k, v in self._latency.items()
            },
            "queues": {
                "units": len(self._waiting),
                "llm": self._llm_q.qsize(),
                "tts": self._tts_q.qsize(),
                "playback": self._playback_queue.qsize(),
            },
            "backpressure": dict(self._backpressure),
            "asr_no_words": self._asr_no_words,
            "playback_short": self._playback_short,
            "warmup": {k: dict(v) for k, v in self._warmup.items()},
            "warmup_pending": sorted(self._warmup_pending),
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
