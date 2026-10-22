"""PCM16 WAV encoding (complete and streaming)."""

from __future__ import annotations

import struct
from typing import Optional

import numpy as np

from ..common.audio import float_to_pcm16

STREAMING_SIZE = 0xFFFFFFFF


def pcm_bytes(audio: np.ndarray) -> bytes:
    """float32 samples in [-1, 1] -> little-endian PCM16 bytes."""
    return float_to_pcm16(audio)


def wav_header(sample_rate: int, data_bytes: Optional[int]) -> bytes:
    """44-byte PCM16 mono header; data_bytes=None gives a streaming header."""
    data_size = STREAMING_SIZE if data_bytes is None else data_bytes
    riff_size = STREAMING_SIZE if data_bytes is None else 36 + data_bytes
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        riff_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        sample_rate,
        sample_rate * 2,
        2,
        16,
        b"data",
        data_size,
    )


def wav_bytes(pcm: bytes, sample_rate: int) -> bytes:
    return wav_header(sample_rate, len(pcm)) + pcm
