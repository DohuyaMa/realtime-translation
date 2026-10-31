"""GET /config schema: every runtime field described, ranges match env readers."""

import pytest

from src.services.asr.settings import AsrSettings
from src.services.common.env import SettingsError
from src.services.common.settings import render_env_value
from src.services.tts.settings import TtsSettings
from src.services.vosk.settings import VoskSettings

CLASSES = [
    (AsrSettings, {"RT_ASR_BACKEND": "fake"}),
    (TtsSettings, {"RT_TTS_BACKEND": "fake"}),
    (VoskSettings, {"RT_VOSK_BACKEND": "fake"}),
]


@pytest.mark.parametrize("cls,base", CLASSES)
def test_every_runtime_field_has_schema(cls, base):
    schema = cls.field_schema()
    assert set(schema) == cls.TUNABLE_FIELDS | cls.RELOAD_FIELDS
    for name, meta in schema.items():
        assert meta["type"] in {
            "bool",
            "int",
            "float",
            "choice",
            "multichoice",
            "model",
            "text",
        }
        assert meta["reload"] == (name in cls.RELOAD_FIELDS)
        if meta["type"] in {"choice", "multichoice", "model"}:
            assert meta["choices"], name


@pytest.mark.parametrize("cls,base", CLASSES)
def test_ranges_match_env_readers(cls, base):
    keys = cls.env_keys()
    for name, meta in cls.field_schema().items():
        if meta["type"] not in {"int", "float"}:
            continue
        key = keys[name]
        for ok in (meta["min"], meta["max"]):
            value = int(ok) if meta["type"] == "int" else ok
            cls.from_env({**base, key: str(value)})
        step = 1 if meta["type"] == "int" else 0.01
        for bad in (meta["min"] - step, meta["max"] + step):
            value = int(bad) if meta["type"] == "int" else bad
            with pytest.raises(SettingsError):
                cls.from_env({**base, key: str(value)})


@pytest.mark.parametrize("cls,base", CLASSES)
def test_every_choice_is_accepted(cls, base):
    keys = cls.env_keys()
    for name, meta in cls.field_schema().items():
        if meta["type"] not in {"choice", "multichoice"} or name in ("device",):
            continue
        for choice in meta["choices"]:
            env = {**base, keys[name]: choice}
            if name == "compute_type":
                continue  # validated by the backend per device
            got = getattr(cls.from_env(env), name)
            if meta.get("nullable") and choice == "":
                assert got is None
            else:
                assert render_env_value(got) == choice


def test_asr_schema_controls():
    schema = AsrSettings.field_schema()
    assert (
        schema["model"]["type"] == "model"
        and "whisper-medium" in schema["model"]["choices"]
    )
    assert (
        schema["language"]["choices"][0] == "" and "uk" in schema["language"]["choices"]
    )
    assert schema["vad_filter"]["type"] == "bool"
    assert (schema["beam_size"]["min"], schema["beam_size"]["max"]) == (1, 10)
    assert TtsSettings.field_schema()["voice"]["type"] == "choice"
