"""KaldiVoskBackend against a stand-in `vosk` module (no Kaldi, no model)."""

import json
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from src.services.vosk.settings import VoskSettings
from src.services.vosk.vosk_backend import FEED_BYTES, KaldiVoskBackend


class FakeModel:
    def __init__(self, path):
        self.path = path


class FakeRecognizer:
    """Ends an utterance after the first fed chunk, then a final one."""

    def __init__(self, model, rate):
        self.model, self.rate, self.fed, self.words = model, rate, 0, False

    def SetWords(self, on):
        self.words = on

    def AcceptWaveform(self, data):
        self.fed += 1
        return self.fed == 1

    def Result(self):
        return json.dumps(
            {
                "text": "добрий день",
                "result": [
                    {"word": "добрий", "conf": 0.912345, "start": 0.1, "end": 0.4},
                    {"word": "день", "conf": 1.0, "start": 0.4, "end": 0.7},
                ],
            }
        )

    def FinalResult(self):
        return json.dumps(
            {
                "text": "друзі",
                "result": [
                    {"word": "друзі", "conf": 0.42, "start": 0.8, "end": 1.2345},
                ],
            }
        )


@pytest.fixture
def fake_vosk(monkeypatch):
    mod = types.ModuleType("vosk")
    mod.Model = FakeModel
    mod.KaldiRecognizer = FakeRecognizer
    mod.SetLogLevel = lambda level: None
    monkeypatch.setitem(sys.modules, "vosk", mod)
    return mod


def test_fetch_load_transcribe_unload(fake_vosk, tmp_path):
    prepared = []

    def prepare(alias, settings, progress):
        prepared.append(alias)
        return tmp_path / alias

    backend = KaldiVoskBackend(VoskSettings.from_env({}), prepare=prepare)
    with pytest.raises(RuntimeError, match="before fetch"):
        backend.load()
    backend.fetch()
    assert prepared == ["vosk-small-uk-v3-nano", "vosk-small-pl-0.22"]
    backend.load()
    assert backend.languages() == ("uk", "pl")
    audio = np.zeros(FEED_BYTES, np.float32)  # 2 feed chunks of PCM16
    result = backend.transcribe(audio, "uk")
    assert result.text == "добрий день друзі"
    assert [(w.word, w.conf, w.end) for w in result.words] == [
        ("добрий", 0.9123, 0.4),
        ("день", 1.0, 0.7),
        ("друзі", 0.42, 1.234),
    ]
    backend.unload()
    assert backend.languages() == ()


def test_models_are_loaded_from_the_prepared_paths(fake_vosk, tmp_path):
    backend = KaldiVoskBackend(
        VoskSettings.from_env({"RT_MODEL": "vosk-small-pl-0.22"}),
        prepare=lambda alias, s, p: tmp_path / alias,
    )
    backend.fetch()
    backend.load()
    assert Path(backend._models["pl"].path) == tmp_path / "vosk-small-pl-0.22"
