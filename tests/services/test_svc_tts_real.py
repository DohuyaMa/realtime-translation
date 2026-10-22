import numpy as np
import pytest

from src.services.tts.kokoro_backend import KokoroBackend
from src.services.tts.settings import TtsSettings


@pytest.mark.slow
@pytest.mark.timeout(600)
def test_kokoro_synthesizes_speech_on_cpu(real_model_dir):
    backend = KokoroBackend(TtsSettings.from_env({}))
    backend.load()
    audio = np.concatenate(
        list(backend.synthesize("Hello world, this is a test.", "af_heart", 1.0))
    )
    assert audio.dtype == np.float32
    assert len(audio) / backend.sample_rate >= 0.5
    assert float(np.max(np.abs(audio))) > 0.01
