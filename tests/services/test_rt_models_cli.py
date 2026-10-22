# tests/services/test_rt_models_cli.py
import hashlib
from pathlib import Path

import pytest

from src.services.common import model_store as ms
from src.services.common import models_cli
from src.services.common.model_registry import FileSpec, ModelSpec

DATA = b"weights"
SPEC = ModelSpec(
    alias="tiny",
    service="asr",
    repo_id="org/tiny",
    revision="c" * 40,
    license="MIT",
    files=(FileSpec("model.bin", len(DATA), hashlib.sha256(DATA).hexdigest()),),
)
REGISTRY = {"tiny": SPEC}


@pytest.fixture(autouse=True)
def _hf_env_sandbox(monkeypatch):
    # main() calls configure_hf_home(); monkeypatch restores HF_* env afterwards.
    for name in (
        "HF_HOME",
        "HF_HUB_OFFLINE",
        "HF_HUB_DISABLE_TELEMETRY",
        "RT_HF_TOKEN_FILE",
    ):
        monkeypatch.setenv(name, "")


def fake_downloader(spec, target, token):
    (Path(target) / "model.bin").write_bytes(DATA)


def store_factory(root):
    return ms.ModelStore(root, downloader=fake_downloader)


def run(tmp_path, *args):
    return models_cli.main(
        ["--model-dir", str(tmp_path), *args],
        registry=REGISTRY,
        store_factory=store_factory,
    )


def test_list_shows_missing_then_ok(tmp_path, capsys):
    assert run(tmp_path, "list") == 0
    assert "tiny\tasr\tMIT\tmissing" in capsys.readouterr().out
    assert run(tmp_path, "fetch", "tiny") == 0
    capsys.readouterr()
    assert run(tmp_path, "list") == 0
    assert "tiny\tasr\tMIT\tok" in capsys.readouterr().out


def test_verify_missing_returns_2(tmp_path):
    assert run(tmp_path, "verify", "tiny") == 2


def test_verify_full_detects_corruption(tmp_path):
    assert run(tmp_path, "fetch", "tiny") == 0
    (tmp_path / "rt" / "tiny" / "model.bin").write_bytes(b"WEIGHTS")
    assert run(tmp_path, "verify", "tiny") == 0
    assert run(tmp_path, "verify", "--full", "tiny") == 3


def test_path_prints_location(tmp_path, capsys):
    assert run(tmp_path, "path", "tiny") == 0
    assert capsys.readouterr().out.strip() == str(tmp_path / "rt" / "tiny")


def test_unknown_alias_returns_2(tmp_path, capsys):
    assert run(tmp_path, "fetch", "nope") == 2
    assert "unknown model alias" in capsys.readouterr().err
