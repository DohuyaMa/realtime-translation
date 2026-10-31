"""Offline stand: replay a WAV through the real rt-asr / rt-vosk streams via
the edge ``StreamClient`` (spec 2026-10-31 §5, step S3).

    python -m scripts.stream_bench session.wav \
        --asr http://127.0.0.1:8091 --vosk http://127.0.0.1:8092 \
        --language uk   # token: --token-file or env RT_API_TOKEN

The WAV (mono, any rate that is a multiple of 16 kHz, or 16 kHz) is fed in
100 ms blocks at ``--speed`` x real time (1 = like a microphone). Printed:
open -> ready per stream, every final with its commit lag (receipt - the
moment its last word's audio was fed), and p50/p90 of the lag per stream.
"""

from __future__ import annotations

import argparse
import os
import threading
import time
import wave
from typing import Dict, List

import numpy as np

from src.audio.resample import Downsampler
from src.clients import Credential, Endpoint, EndpointPool, client_factories
from src.clients.base import read_token
from src.clients.stream import RATE, StreamClient
from src.core.runtime import EdgeSettings


def read_wav(path: str) -> np.ndarray:
    """16 kHz PCM16 mono."""
    with wave.open(path, "rb") as w:
        rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        data = w.readframes(w.getnframes())
    if width != 2:
        raise SystemExit(f"{path}: only 16-bit PCM WAV is supported")
    audio = np.frombuffer(data, dtype="<i2").reshape(-1, channels)[:, 0]
    if rate == RATE:
        return audio.copy()
    return Downsampler(rate)(audio.astype(np.float32) / 32768.0)


def pct(values: List[float], q: float):
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(q * len(ordered)))], 3)


def make_pool(kind: str, url: str, token: str) -> EndpointPool:
    f = client_factories(EdgeSettings())
    make = f.asr if kind == "asr" else f.vosk
    return EndpointPool(kind, [Endpoint(kind, "bench", url, Credential(token))], make)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("wav")
    ap.add_argument("--asr", default="http://127.0.0.1:8091")
    ap.add_argument("--vosk", default="http://127.0.0.1:8092", help="'' = off")
    ap.add_argument("--token-file", default=None)
    ap.add_argument("--language", default="uk")
    ap.add_argument("--speed", type=float, default=1.0)
    args = ap.parse_args()

    audio = read_wav(args.wav)
    token = read_token(args.token_file) or os.environ.get("RT_API_TOKEN", "")
    fed_at: List[tuple] = []  # (stream seconds fed, monotonic)
    lags: Dict[str, List[float]] = {}
    lock = threading.Lock()

    def wall(t: float) -> float:
        with lock:
            for s, at in fed_at:
                if s >= t:
                    return at
        return time.monotonic()

    def on_event(kind: str, msg: dict) -> None:
        now = time.monotonic()
        if msg["type"] == "final":
            lag = now - wall(msg["end"])
            lags.setdefault(kind, []).append(lag)
            print(
                f"{kind:4} final [{msg['start']:7.2f}-{msg['end']:7.2f}]"
                f" lag {lag:5.2f}s{' eos' if msg.get('eos') else ''}: {msg['text']}"
            )
        elif msg["type"] in ("ready", "down", "failed"):
            print(f"{kind:4} {msg['type']}: {msg}")

    streams = [("asr", args.asr)] + ([("vosk", args.vosk)] if args.vosk else [])
    clients = [
        StreamClient(
            kind, make_pool(kind, url, token), language=args.language, on_event=on_event
        )
        for kind, url in streams
    ]
    t_click = time.monotonic()
    for c in clients:
        c.start()
    block = RATE // 10
    for i in range(0, len(audio), block):
        chunk = audio[i : i + block]
        for c in clients:
            c.feed(chunk)
        with lock:
            fed_at.append(((i + len(chunk)) / RATE, time.monotonic()))
        if args.speed > 0:
            time.sleep(len(chunk) / RATE / args.speed)
    for c in clients:
        c.stop(timeout_s=10.0)
    print(f"audio {len(audio) / RATE:.1f} s, wall {time.monotonic() - t_click:.1f} s")
    for c in clients:
        k = c.kind
        print(
            f"{k}: ready {c.ready_ms} ms, finals {len(lags.get(k, []))},"
            f" commit lag p50 {pct(lags.get(k, []), 0.5)} s"
            f" p90 {pct(lags.get(k, []), 0.9)} s, {c.stats()}"
        )


if __name__ == "__main__":
    main()
