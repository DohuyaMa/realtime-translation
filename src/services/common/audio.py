"""Audio helpers: file decoding, PCM16 <-> float32, resampling."""

from __future__ import annotations

import math
import os

import numpy as np

TARGET_RATE = 16000


class AudioDecodeError(ValueError):
    pass


def resample(audio: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate:
        return audio.astype(np.float32, copy=False)
    from scipy.signal import resample_poly

    g = math.gcd(src_rate, dst_rate)
    return resample_poly(audio, dst_rate // g, src_rate // g).astype(np.float32)


def decode_audio_file(data: bytes, target_rate: int = TARGET_RATE) -> np.ndarray:
    """Any libsndfile format (WAV/FLAC/OGG/MP3) -> float32 mono at target_rate."""
    if not data:
        raise AudioDecodeError("empty audio file")
    import soundfile as sf

    # Decode from a file descriptor (sf_open_fd), not a BytesIO: soundfile's
    # virtual IO needs cffi callbacks = write+execute memory, which the
    # systemd unit forbids (MemoryDenyWriteExecute). An anonymous memfd keeps
    # the upload in RAM, no temp file on disk.
    fd = os.memfd_create("rt-upload", os.MFD_CLOEXEC)
    try:
        with os.fdopen(fd, "w+b", closefd=False) as f:
            f.write(data)
        os.lseek(fd, 0, os.SEEK_SET)
        with sf.SoundFile(fd, closefd=False) as snd:
            rate = snd.samplerate
            audio = snd.read(dtype="float32", always_2d=True)
    except Exception as exc:  # noqa: BLE001 - libsndfile raises various types
        raise AudioDecodeError(f"cannot decode audio: {exc}") from exc
    finally:
        os.close(fd)
    if audio.shape[0] == 0:
        raise AudioDecodeError("audio has no samples")
    return resample(audio.mean(axis=1), int(rate), target_rate)


def pcm16_to_float(data: bytes) -> np.ndarray:
    usable = len(data) - (len(data) % 2)
    return np.frombuffer(data[:usable], dtype="<i2").astype(np.float32) / 32768.0


def float_to_pcm16(audio: np.ndarray) -> bytes:
    return (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
