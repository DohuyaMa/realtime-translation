import numpy as np
import pytest

from src.services.common.audio import (
    AudioDecodeError,
    decode_audio_file,
    float_to_pcm16,
    pcm16_to_float,
    resample,
)
from svc_testutil import tone, wav_file


def test_decode_wav_16k_mono():
    audio = decode_audio_file(wav_file(tone(16000)))
    assert audio.dtype == np.float32 and audio.shape == (16000,)
    assert abs(float(np.max(np.abs(audio))) - 0.1) < 0.01


def test_decode_stereo_48k_flac_to_16k_mono():
    stereo = np.stack([tone(48000, sr=48000), tone(48000, sr=48000)], axis=1)
    audio = decode_audio_file(wav_file(stereo, sr=48000, fmt="FLAC"))
    assert audio.shape == (16000,)


@pytest.mark.parametrize("data", [b"", b"not audio at all"])
def test_decode_rejects_garbage(data):
    with pytest.raises(AudioDecodeError):
        decode_audio_file(data)


def test_pcm_roundtrip_and_odd_bytes():
    x = np.array([0.0, 0.5, -0.5, 1.5], dtype=np.float32)
    back = pcm16_to_float(float_to_pcm16(x))
    assert np.allclose(back, [0.0, 0.5, -0.5, 1.0], atol=1e-3)
    assert pcm16_to_float(b"\x00\x00\x01").shape == (1,)


def test_resample_identity_and_ratio():
    x = tone(24000, sr=24000)
    assert resample(x, 24000, 24000) is not None
    assert resample(x, 24000, 16000).shape == (16000,)


def test_decode_uses_fd_not_virtual_io(monkeypatch):
    """BytesIO needs cffi callbacks (W+X memory), forbidden by the unit's
    MemoryDenyWriteExecute; decoding goes through an fd (memfd)."""
    import io
    import wave

    import numpy as np
    import soundfile as sf

    from src.services.common.audio import decode_audio_file

    seen = []
    real = sf.SoundFile

    def spy(file, *a, **kw):
        seen.append(type(file))
        return real(file, *a, **kw)

    monkeypatch.setattr(sf, "SoundFile", spy)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(np.zeros(1600, "<i2").tobytes())
    assert decode_audio_file(buf.getvalue()).shape == (1600,)
    assert seen == [int]
