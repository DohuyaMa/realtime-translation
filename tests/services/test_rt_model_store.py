# tests/services/test_rt_model_store.py
import hashlib
import json
import os
import threading
import time
from pathlib import Path

import pytest

from src.services.common import model_store as ms
from src.services.common.model_registry import FileSpec, ModelSpec

FILES = {"model.bin": b"weights-123", "sub/config.json": b'{"a": 1}'}


def make_spec(files=FILES, alias="m", revision="a" * 40):
    return ModelSpec(
        alias=alias,
        service="asr",
        repo_id="org/m",
        revision=revision,
        license="MIT",
        files=tuple(
            FileSpec(p, len(b), hashlib.sha256(b).hexdigest()) for p, b in files.items()
        ),
    )


class FakeDownloader:
    def __init__(self, files=FILES, corrupt=None, fail=None, delay=0.0):
        self.files, self.corrupt, self.fail, self.delay = (
            files,
            corrupt or {},
            fail,
            delay,
        )
        self.calls = 0
        self.tokens = []

    def __call__(self, spec, target, token):
        self.calls += 1
        self.tokens.append(token)
        time.sleep(self.delay)
        if self.fail:
            raise self.fail
        for path, data in self.files.items():
            dst = Path(target) / path
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(self.corrupt.get(path, data))


def test_ensure_downloads_and_writes_manifest(tmp_path):
    dl = FakeDownloader()
    store = ms.ModelStore(tmp_path, downloader=dl)
    path = store.ensure(make_spec(), download=True, token="tok")
    assert path == tmp_path / "rt" / "m"
    assert (path / "model.bin").read_bytes() == b"weights-123"
    manifest = json.loads((path / ms.MANIFEST).read_text())
    assert manifest["revision"] == "a" * 40
    assert (
        manifest["files"]["model.bin"]["sha256"]
        == hashlib.sha256(b"weights-123").hexdigest()
    )
    assert dl.tokens == ["tok"]
    assert not (tmp_path / "rt" / "m.partial").exists()


def test_ensure_is_idempotent(tmp_path):
    dl = FakeDownloader()
    store = ms.ModelStore(tmp_path, downloader=dl)
    store.ensure(make_spec(), download=True)
    store.ensure(make_spec(), download=True)
    assert dl.calls == 1


def test_ensure_without_download_raises(tmp_path):
    store = ms.ModelStore(tmp_path, downloader=FakeDownloader())
    with pytest.raises(ms.ModelNotProvisioned, match="rt-models fetch m"):
        store.ensure(make_spec(), download=False)


def test_corrupt_download_is_rejected(tmp_path):
    dl = FakeDownloader(corrupt={"model.bin": b"weights-XYZ"})
    store = ms.ModelStore(tmp_path, downloader=dl)
    with pytest.raises(ms.ModelIntegrityError, match="model.bin"):
        store.ensure(make_spec(), download=True)
    assert not (tmp_path / "rt" / "m").exists()
    assert not (tmp_path / "rt" / "m.partial").exists()


def test_failed_download_cleans_partial(tmp_path):
    store = ms.ModelStore(tmp_path, downloader=FakeDownloader(fail=OSError("net down")))
    with pytest.raises(ms.ModelNotProvisioned, match="net down"):
        store.ensure(make_spec(), download=True)
    assert not (tmp_path / "rt" / "m.partial").exists()


def test_stale_partial_is_replaced(tmp_path):
    junk = tmp_path / "rt" / "m.partial"
    junk.mkdir(parents=True)
    (junk / "junk.bin").write_bytes(b"old")
    path = ms.ModelStore(tmp_path, downloader=FakeDownloader()).ensure(
        make_spec(), download=True
    )
    assert not junk.exists()
    assert not (path / "junk.bin").exists()


def test_verify_quick_vs_full(tmp_path):
    store = ms.ModelStore(tmp_path, downloader=FakeDownloader())
    spec = make_spec()
    path = store.ensure(spec, download=True)
    (path / "model.bin").write_bytes(b"weights-999")  # same size, different content
    assert store.verify(spec, full=False) == []
    problems = store.verify(spec, full=True)
    assert problems and problems[0][0] == "mismatch"


def test_revision_change_triggers_redownload(tmp_path):
    dl = FakeDownloader()
    store = ms.ModelStore(tmp_path, downloader=dl)
    store.ensure(make_spec(), download=True)
    store.ensure(make_spec(revision="b" * 40), download=True)
    assert dl.calls == 2


def test_concurrent_ensure_downloads_once(tmp_path):
    dl = FakeDownloader(delay=0.3)
    spec = make_spec()
    errors = []

    def worker():
        try:
            ms.ModelStore(tmp_path, downloader=dl).ensure(spec, download=True)
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert dl.calls == 1


def test_resolve_alias_uses_lookup(tmp_path):
    store = ms.ModelStore(tmp_path, downloader=FakeDownloader())
    path = ms.resolve_model(
        "m", store, download=True, lookup=lambda alias: make_spec(alias=alias)
    )
    assert path == tmp_path / "rt" / "m"


def test_resolve_abs_path_without_manifest(tmp_path, caplog):
    model_dir = tmp_path / "own"
    model_dir.mkdir()
    store = ms.ModelStore(tmp_path / "store", downloader=FakeDownloader())
    assert ms.resolve_model(str(model_dir), store, download=False) == model_dir
    assert "unmanaged" in caplog.text


def test_resolve_abs_path_missing(tmp_path):
    store = ms.ModelStore(tmp_path, downloader=FakeDownloader())
    with pytest.raises(ms.ModelNotProvisioned, match="does not exist"):
        ms.resolve_model(str(tmp_path / "nope"), store, download=False)


def test_resolve_abs_path_file_rejected(tmp_path):
    f = tmp_path / "model.bin"
    f.write_bytes(b"x")
    store = ms.ModelStore(tmp_path / "store", downloader=FakeDownloader())
    with pytest.raises(ms.ModelNotProvisioned, match="not a directory"):
        ms.resolve_model(str(f), store, download=False)


def test_resolve_abs_path_with_bad_manifest(tmp_path):
    model_dir = tmp_path / "own"
    model_dir.mkdir()
    (model_dir / "model.bin").write_bytes(b"short")
    (model_dir / ms.MANIFEST).write_text(
        json.dumps({"files": {"model.bin": {"size": 999, "sha256": "0" * 64}}})
    )
    store = ms.ModelStore(tmp_path / "store", downloader=FakeDownloader())
    with pytest.raises(ms.ModelIntegrityError, match="model.bin"):
        ms.resolve_model(str(model_dir), store, download=False)


def test_settings_from_env_defaults(monkeypatch):
    s = ms.ModelSettings.from_env({})
    assert s.model_dir == Path.home() / ".cache" / "rt-speech" / "models"
    assert s.download is True and s.full_verify is False and s.token_file is None


def test_settings_from_env_values(tmp_path):
    s = ms.ModelSettings.from_env(
        {
            "RT_MODEL_DIR": str(tmp_path),
            "RT_MODEL_DOWNLOAD": "false",
            "RT_MODEL_VERIFY": "FULL",
            "RT_HF_TOKEN_FILE": "/run/secrets/hg-token",
        }
    )
    assert s.model_dir == tmp_path and s.download is False and s.full_verify is True
    assert s.token_file == "/run/secrets/hg-token"


def test_settings_reject_bad_values():
    with pytest.raises(ValueError, match="RT_MODEL_DOWNLOAD"):
        ms.ModelSettings.from_env({"RT_MODEL_DOWNLOAD": "maybe"})
    with pytest.raises(ValueError, match="RT_MODEL_VERIFY"):
        ms.ModelSettings.from_env({"RT_MODEL_VERIFY": "deep"})


def test_prepare_model_sets_offline(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("HF_HOME", raising=False)
    model_dir = tmp_path / "own"
    model_dir.mkdir()
    settings = ms.ModelSettings(
        model_dir=tmp_path / "models",
        download=False,
        full_verify=False,
        token_file=None,
    )
    assert ms.prepare_model(str(model_dir), settings) == model_dir
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["HF_HOME"] == str(tmp_path / "models" / "hf")
