# tests/services/test_rt_hf_env.py
import os
import sys
import types

import pytest

from src.services.common import hf_env


def test_configure_hf_home(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_HOME", raising=False)
    monkeypatch.delenv("HF_HUB_DISABLE_TELEMETRY", raising=False)
    hf = hf_env.configure_hf_home(tmp_path)
    assert hf == tmp_path / "hf"
    assert hf.is_dir()
    assert os.environ["HF_HOME"] == str(hf)
    assert os.environ["HF_HUB_DISABLE_TELEMETRY"] == "1"


def test_set_offline(monkeypatch):
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    hf_env.set_offline()
    assert os.environ["HF_HUB_OFFLINE"] == "1"


def test_set_offline_patches_imported_hub(monkeypatch):
    fake_constants = types.SimpleNamespace(HF_HUB_OFFLINE=False)
    monkeypatch.setitem(sys.modules, "huggingface_hub.constants", fake_constants)
    hf_env.set_offline()
    assert fake_constants.HF_HUB_OFFLINE is True


def test_read_token_file(tmp_path):
    f = tmp_path / "tok"
    f.write_text("  hf_abc123\n")
    assert hf_env.read_token_file(str(f)) == "hf_abc123"


def test_read_token_file_none():
    assert hf_env.read_token_file(None) is None
    assert hf_env.read_token_file("") is None


def test_read_token_file_missing(tmp_path):
    with pytest.raises(FileNotFoundError, match="RT_HF_TOKEN_FILE"):
        hf_env.read_token_file(str(tmp_path / "missing"))


def test_read_token_file_empty(tmp_path):
    f = tmp_path / "tok"
    f.write_text("\n")
    with pytest.raises(ValueError, match="empty"):
        hf_env.read_token_file(str(f))


def test_set_online_clears_env_and_hub_constant(monkeypatch):
    fake_constants = types.SimpleNamespace(HF_HUB_OFFLINE=True)
    monkeypatch.setitem(sys.modules, "huggingface_hub.constants", fake_constants)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    hf_env.set_online()
    assert "HF_HUB_OFFLINE" not in os.environ
    assert fake_constants.HF_HUB_OFFLINE is False
