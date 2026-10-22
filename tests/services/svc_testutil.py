"""Helpers for tests/services (importable: tests/services has no __init__)."""

import io
from contextlib import contextmanager

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

SR = 16000
WINDOW = 512


def tone(
    n_samples: int, amp: float = 0.1, freq: float = 220.0, sr: int = SR
) -> np.ndarray:
    t = np.arange(n_samples) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def pcm(audio: np.ndarray) -> bytes:
    return (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()


def speech_windows(n: int) -> bytes:
    """n * 32 ms of loud tone (speech for EnergyVad)."""
    return pcm(tone(n * WINDOW))


def silence_windows(n: int) -> bytes:
    return bytes(n * WINDOW * 2)


def wav_file(audio: np.ndarray, sr: int = SR, fmt: str = "WAV") -> bytes:
    buf = io.BytesIO()
    sf.write(buf, audio, sr, format=fmt)
    return buf.getvalue()


@contextmanager
def running(app, timeout: float = 5.0):
    """TestClient with lifespan running and the (fake) model loaded."""
    with TestClient(app) as client:
        assert app.state.runtime.wait_ready(timeout), app.state.runtime.error
        yield client
