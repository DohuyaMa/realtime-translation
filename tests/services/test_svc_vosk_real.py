"""Real Vosk on a provisioned model; skipped unless the model is in
RT_MODEL_DIR (`rt-models fetch vosk-small-pl-0.22`), `vosk` is importable and
espeak-ng can synthesize the test phrase."""

import shutil
import subprocess

import pytest

from src.services.common.audio import decode_audio_file
from src.services.common.model_registry import get_spec
from src.services.common.model_store import ModelSettings

ALIAS = "vosk-small-pl-0.22"


@pytest.mark.timeout(120)
def test_vosk_transcribes_polish_speech(tmp_path):
    pytest.importorskip("vosk")
    if shutil.which("espeak-ng") is None:
        pytest.skip("espeak-ng not on PATH")
    if ModelSettings.from_env().store().verify(get_spec(ALIAS)):
        pytest.skip(f"{ALIAS} not provisioned (rt-models fetch {ALIAS})")
    from src.services.vosk.settings import VoskSettings
    from src.services.vosk.vosk_backend import KaldiVoskBackend

    wav = tmp_path / "pl.wav"
    subprocess.run(
        [
            "espeak-ng",
            "-v",
            "pl",
            "-s",
            "140",
            "-w",
            str(wav),
            "Dzień dobry. Dzisiaj sprawdzamy rozpoznawanie mowy po polsku.",
        ],
        check=True,
    )
    backend = KaldiVoskBackend(VoskSettings.from_env({"RT_MODEL": ALIAS}))
    backend.fetch()
    backend.load()
    result = backend.transcribe(decode_audio_file(wav.read_bytes()), "pl")
    backend.unload()
    assert "dobry" in result.text and "polsku" in result.text, result.text
    assert all(0.0 <= w.conf <= 1.0 and w.start < w.end for w in result.words)
