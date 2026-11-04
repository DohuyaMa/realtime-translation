"""Real wav2vec2 CTC on the provisioned model, cpu path (onnxruntime int8;
the first run exports the ONNX copy into the model dir, ~1 min); skipped
unless the model is in RT_MODEL_DIR (`rt-models fetch uk-wav2vec2-stress`),
torch/transformers/onnx/onnxruntime are importable and espeak-ng can
synthesize the test phrase."""

import shutil
import subprocess

import numpy as np
import pytest

from src.services.common.audio import decode_audio_file
from src.services.common.model_registry import get_spec
from src.services.common.model_store import ModelSettings

ALIAS = "uk-wav2vec2-stress"


@pytest.mark.timeout(900)
def test_ctc_transcribes_ukrainian_speech_and_silence_is_empty(tmp_path):
    pytest.importorskip("torch")
    pytest.importorskip("transformers")
    pytest.importorskip("onnx")
    pytest.importorskip("onnxruntime")
    if shutil.which("espeak-ng") is None:
        pytest.skip("espeak-ng not on PATH")
    if ModelSettings.from_env().store().verify(get_spec(ALIAS)):
        pytest.skip(f"{ALIAS} not provisioned (rt-models fetch {ALIAS})")
    from src.services.phonetic.settings import PhoneticSettings
    from src.services.phonetic.wav2vec2_backend import Wav2Vec2Backend

    wav = tmp_path / "uk.wav"
    subprocess.run(
        ["espeak-ng", "-v", "uk", "-s", "140", "-w", str(wav), "Добрий день."],
        check=True,
    )
    backend = Wav2Vec2Backend(PhoneticSettings.from_env({"RT_MODEL": ALIAS}))
    backend.fetch()
    backend.load()
    result = backend.transcribe(decode_audio_file(wav.read_bytes()), "uk")
    silence = backend.transcribe(np.zeros(32000, np.float32), "uk")
    backend.unload()
    assert result.text and all(ch.isalpha() or ch in " ’'" for ch in result.text)
    assert result.text == result.text.lower() and "+" not in result.text
    assert all(w.start < w.end for w in result.words)
    assert silence.text == "" and silence.words == ()
