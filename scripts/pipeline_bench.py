"""Offline end-to-end stand: a WAV through the full edge pipeline (stream
-> units -> phonetic gate -> LLM -> TTS -> playback) with the real services,
without an audio device (spec 2026-10-31 §5).

    RT_API_TOKEN=... RT_LLM_TOKEN=... python -m scripts.pipeline_bench talk.wav \
        --language uk [--phonetic ''] [--log units.log] [--gain-db -20]

The WAV (mono 16-bit, 48 kHz or any multiple of 16 kHz) is "captured" in
1024-sample blocks at real-time pace; playback is a sink that waits the
audio's length (so its backlog is real) and records nothing else. Printed:
warm-up (Start -> all ready), p50/p90 of speech end -> commit, LLM first
sentence, TTS first audio and speech end -> playback, the unit/gate counters.
"""

from __future__ import annotations

import argparse
import os
import threading
import time
import wave
from typing import Any, Callable, Optional

import numpy as np
from loguru import logger

from src.clients import Credential, Directory, Endpoint, EndpointPool, client_factories
from src.clients.pool import SpeechPools
from src.core.runtime import EdgeSettings, LlmSettings, LocalUrls, ServiceUse
from src.translation_system import BLOCKSIZE, TranslationSystem


class WavCapture:
    """``capture_factory``: plays the WAV into the pipeline at real time."""

    def __init__(self, path: str, gain_db: float = 0.0) -> None:
        with wave.open(path, "rb") as w:
            self.rate = w.getframerate()
            data = w.readframes(w.getnframes())
            channels = w.getnchannels()
        pcm = np.frombuffer(data, dtype="<i2").reshape(-1, channels)[:, 0]
        self.audio = pcm.astype(np.float32) / 32768.0 * 10.0 ** (gain_db / 20.0)
        self.done = threading.Event()

    def __call__(self, on_block: Callable[[np.ndarray], None]) -> "WavCapture":
        self._on_block = on_block
        return self

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        t0 = time.monotonic()
        for i in range(0, len(self.audio), BLOCKSIZE):
            self._on_block(self.audio[i : i + BLOCKSIZE].copy())
            ahead = t0 + (i + BLOCKSIZE) / self.rate - time.monotonic()
            if ahead > 0:
                time.sleep(ahead)
        self.done.set()

    def stop(self) -> None:
        pass

    def close(self) -> None:
        pass


class Target:
    def __init__(self, url: str, token: str) -> None:
        self.url, self.token = url, token

    def current(self) -> tuple:
        return self.url, self.token

    def invalidate(self, kind: str) -> None:
        pass


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("wav")
    ap.add_argument("--language", default="uk")
    ap.add_argument("--asr", default="http://127.0.0.1:8091")
    ap.add_argument("--phonetic", default="http://127.0.0.1:8092", help="'' = off")
    ap.add_argument("--tts", default="http://127.0.0.1:8093")
    ap.add_argument("--llm", default="http://127.0.0.1:8080")
    ap.add_argument("--log", default=None, help="INFO log file (units, timing)")
    ap.add_argument(
        "--gain-db",
        type=float,
        default=0.0,
        help="scale the WAV first, e.g. -20 = a quiet microphone",
    )
    args = ap.parse_args()

    logger.remove()
    logger.add(lambda m: print(m, end=""), level="WARNING")
    if args.log:
        logger.add(args.log, level="INFO")
    token = os.environ.get("RT_API_TOKEN", "")
    settings = EdgeSettings(
        mode="translate",
        source_language=args.language,
        local=LocalUrls(
            llm=args.llm,
            asr=args.asr,
            tts=args.tts,
            phonetic=args.phonetic or None,
        ),
        use=ServiceUse(phonetic=None if args.phonetic else "off"),
        llm=LlmSettings(),
    )
    f = client_factories(settings)

    def pool(kind: str, url: str, make: Any) -> EndpointPool:
        return EndpointPool(
            kind, [Endpoint(kind, "bench", url, Credential(token))], make
        )

    def pools(_s: EdgeSettings, _d: Directory) -> SpeechPools:
        return SpeechPools(
            asr=pool("asr", args.asr, f.asr),
            tts=pool("tts", args.tts, f.tts),
            phonetic=(
                pool("phonetic", args.phonetic, f.phonetic) if args.phonetic else None
            ),
        )

    capture = WavCapture(args.wav, args.gain_db)
    played = []

    def sink(audio: np.ndarray, sr: int) -> None:
        played.append(len(audio) / sr)
        time.sleep(len(audio) / sr)  # a real device takes this long

    ts = TranslationSystem(
        settings,
        directory_factory=lambda s: Directory(),
        pools_factory=pools,
        llm_target_factory=lambda s, d: Target(
            args.llm, os.environ.get("RT_LLM_TOKEN", "")
        ),
        capture_factory=capture,
        playback_sink=sink,
    )
    if not ts.start():
        raise SystemExit(f"start failed: {ts.last_error}")
    capture.done.wait()
    time.sleep(2.0)  # the last words: endpoint + commit
    ts.stop()
    stats = ts.get_stats()
    ts.cleanup()
    audio_s = len(capture.audio) / capture.rate
    warm: Optional[dict] = stats["warmup"]
    print(f"audio {audio_s:.1f} s, played {sum(played):.1f} s in {len(played)} pieces")
    print("warm-up:", {k: (v["ms"], v["error"]) for k, v in (warm or {}).items()})
    for key, v in stats["latency"].items():
        print(f"{key:14} p50 {v['p50']} ms  p90 {v['p90']} ms  (n {v['n']})")
    keys = (
        "units_processed",
        "llm_requests",
        "llm_rejected",
        "phonetic_checked",
        "gate_dropped",
        "gate_foreign_terms",
        "whisper_only",
        "phonetic_unavailable",
        "phonetic_timeout",
        "retry_whisper_only",
        "rejected_after_tts",
        "retry_incomplete",
        "spoken_incomplete",
        "asr_no_words",
        "pipeline_errors",
        "backpressure",
        "playback_backlog_s",
        "tts_catchup",
        "units_short_merged",
        "mic",
    )
    print({k: stats[k] for k in keys})


if __name__ == "__main__":
    main()
