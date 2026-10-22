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
    assert {s.alias for s in mr.specs_for("asr")} == {
        "whisper-small",
        "whisper-large-v3-turbo",
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
