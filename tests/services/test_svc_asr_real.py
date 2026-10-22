import time

import numpy as np
import pytest
from loguru import logger

from src.services.asr.app import create_app
from src.services.asr.backend import DecodeOptions
from src.services.asr.faster_whisper_backend import FasterWhisperBackend
from src.services.asr.settings import AsrSettings
from src.services.common.audio import resample
from src.services.tts.kokoro_backend import KokoroBackend
from src.services.tts.settings import TtsSettings
from svc_testutil import running, wav_file

PHRASE = "Hello world, this is a test."


def _speech_16k() -> np.ndarray:
    tts = KokoroBackend(TtsSettings.from_env({}))
    tts.load()
    speech = np.concatenate(list(tts.synthesize(PHRASE, "af_heart", 1.0)))
    return resample(speech, tts.sample_rate, 16000)


@pytest.mark.slow
@pytest.mark.timeout(900)
def test_faster_whisper_small_transcribes_kokoro_speech(real_model_dir):
    audio = _speech_16k()
    asr = FasterWhisperBackend(AsrSettings.from_env({"RT_MODEL": "whisper-small"}))
    asr.load()
    t0 = time.monotonic()
    result = asr.transcribe(
        audio, DecodeOptions(language="en", prompt=None, beam_size=5)
    )
    logger.info(
        "small decode of {:.1f}s audio took {:.2f}s",
        len(audio) / 16000,
        time.monotonic() - t0,
    )
    assert "hello" in result.text.lower()
    assert result.language == "en"


@pytest.mark.slow
@pytest.mark.timeout(1800)
def test_long_audio_chunked_real(real_model_dir):
    short = _speech_16k()
    gap = np.zeros(8000, dtype=np.float32)  # 0.5 s pause between repetitions
    unit = np.concatenate([short, gap])
    audio = np.tile(unit, int(np.ceil(70 * 16000 / len(unit))))[: 70 * 16000]

    env = {
        "RT_ASR_BACKEND": "faster-whisper",
        "RT_DEVICE": "cpu",
        "RT_MODEL": "whisper-small",
    }
    settings = AsrSettings.from_env(env)
    app = create_app(settings, bootstrap_env=env, state_path=None)
    with running(app, timeout=600) as client:
        r = client.post(
            "/v1/audio/transcriptions",
            files={"file": ("long.wav", wav_file(audio), "audio/wav")},
            data={
                "language": "en",
                "priority": "batch",
                "chunk_seconds": "30",
                "response_format": "verbose_json",
            },
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["chunks"] == 3
    assert body["text"].strip()
