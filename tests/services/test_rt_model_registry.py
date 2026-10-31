# tests/services/test_rt_model_registry.py
import re

import pytest

from src.services.common import model_registry as mr

DEFAULT_ALIASES = [
    "whisper-small",
    "whisper-large-v3-turbo",
    "kokoro-82m-v1.0-onnx",
]


def test_aliases_unique():
    assert len(mr.REGISTRY) == len(mr._SPECS)


def test_every_spec_is_pinned():
    for spec in mr._SPECS:
        assert re.fullmatch(r"[0-9a-f]{40}", spec.revision), spec.alias
        assert spec.service in {"asr", "tts"}
        assert spec.files, spec.alias
        for f in spec.files:
            assert re.fullmatch(r"[0-9a-f]{64}", f.sha256), (spec.alias, f.path)
            assert f.size > 0
            assert not f.path.startswith("/") and ".." not in f.path


@pytest.mark.parametrize("alias", DEFAULT_ALIASES)
def test_default_aliases_present(alias):
    assert mr.get_spec(alias).alias == alias


def test_unknown_alias():
    with pytest.raises(mr.UnknownModel, match="unknown model alias 'nope'"):
        mr.get_spec("nope")


def test_specs_for_service():
    # the faster-whisper catalog (faster_whisper.utils._MODELS, aliases deduped)
    assert {s.alias for s in mr.specs_for("asr")} == {
        "whisper-tiny",
        "whisper-tiny.en",
        "whisper-base",
        "whisper-base.en",
        "whisper-small",
        "whisper-small.en",
        "whisper-medium",
        "whisper-medium.en",
        "whisper-large-v1",
        "whisper-large-v2",
        "whisper-large-v3",
        "whisper-large-v3-turbo",
        "whisper-distil-large-v2",
        "whisper-distil-large-v3",
        "whisper-distil-large-v3.5",
        "whisper-distil-medium.en",
        "whisper-distil-small.en",
    }
    assert {s.alias for s in mr.specs_for("tts")} == {"kokoro-82m-v1.0-onnx"}


def test_onnx_spec_has_required_files():
    paths = {f.path for f in mr.get_spec("kokoro-82m-v1.0-onnx").files}
    assert {
        "onnx/model.onnx",
        "onnx/model_fp16.onnx",
        "onnx/model_quantized.onnx",
        "tokenizer.json",
        "voices/af_heart.bin",
        "voices/bf_emma.bin",
    } <= paths


def test_allow_patterns_are_file_paths():
    spec = mr.get_spec("whisper-small")
    assert spec.allow_patterns == [f.path for f in spec.files]


def test_every_faster_whisper_repo_is_pinned():
    pytest.importorskip("faster_whisper")
    from faster_whisper.utils import _MODELS

    pinned = {s.repo_id for s in mr.specs_for("asr")}
    # large-v3-turbo is pinned from the dropbox-dash mirror of the same weights
    expected = set(_MODELS.values()) - {"mobiuslabsgmbh/faster-whisper-large-v3-turbo"}
    assert expected <= pinned
