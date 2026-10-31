"""48 kHz capture -> 16 kHz PCM16 for the WS streams (audio/resample.py)."""

import numpy as np

from src.audio.resample import Downsampler, rms


def test_tone_survives_and_block_size_does_not_matter():
    t = np.arange(48000) / 48000.0
    x = (0.5 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
    whole = Downsampler()(x)
    d = Downsampler()
    parts = np.concatenate([d(x[i : i + 1024]) for i in range(0, len(x), 1024)])
    assert len(whole) == len(parts) == 16000
    assert np.array_equal(whole, parts)
    spectrum = np.abs(np.fft.rfft(whole[1000:].astype(float)))
    assert abs(np.argmax(spectrum) * 16000 / len(whole[1000:]) - 1000) < 5
    assert 0.3 < rms(whole[1000:] / 32768.0) < 0.4


def test_alias_above_8k_is_filtered():
    t = np.arange(48000) / 48000.0
    x = (0.5 * np.sin(2 * np.pi * 12000 * t)).astype(np.float32)
    assert rms(Downsampler()(x)[1000:] / 32768.0) < 0.01
