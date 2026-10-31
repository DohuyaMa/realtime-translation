# tests/services/test_rt_model_store.py
import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from src.services.common import model_store as ms
from src.services.common.model_registry import ArchiveSource, FileSpec, ModelSpec

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


def test_prepare_model_is_online_during_download_and_offline_after(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")  # left over from an earlier load
    seen = []

    def fake_resolve(value, store, **kwargs):
        seen.append(os.environ.get("HF_HUB_OFFLINE"))
        return tmp_path

    monkeypatch.setattr(ms, "resolve_model", fake_resolve)
    settings = ms.ModelSettings(
        model_dir=tmp_path, download=True, full_verify=False, token_file=None
    )
    ms.prepare_model("m", settings)
    assert seen == [None]
    assert os.environ["HF_HUB_OFFLINE"] == "1"


def test_prepare_model_stays_offline_when_download_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    seen = []
    monkeypatch.setattr(
        ms,
        "resolve_model",
        lambda value, store, **kw: seen.append(os.environ.get("HF_HUB_OFFLINE"))
        or tmp_path,
    )
    settings = ms.ModelSettings(
        model_dir=tmp_path, download=False, full_verify=False, token_file=None
    )
    ms.prepare_model("m", settings)
    assert seen == ["1"]


def _hub_constants(monkeypatch):
    import types

    fake = types.SimpleNamespace(HF_HUB_OFFLINE=True)
    monkeypatch.setitem(sys.modules, "huggingface_hub.constants", fake)
    return fake


def test_prepare_model_download_online_then_offline_even_on_failure(
    tmp_path, monkeypatch
):
    constants = _hub_constants(monkeypatch)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    seen = []

    def downloader(spec, target, token):
        seen.append((os.environ.get("HF_HUB_OFFLINE"), constants.HF_HUB_OFFLINE))
        raise RuntimeError("network down")

    monkeypatch.setattr(
        ms.ModelSettings,
        "store",
        lambda self: ms.ModelStore(self.model_dir, downloader=downloader),
    )
    settings = ms.ModelSettings(
        model_dir=tmp_path, download=True, full_verify=False, token_file=None
    )
    with pytest.raises(ms.ModelNotProvisioned):
        ms.prepare_model("whisper-small", settings)
    assert seen == [(None, False)]
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert constants.HF_HUB_OFFLINE is True


def test_ensure_reports_download_progress_from_bytes_on_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(ms, "DOWNLOAD_POLL_S", 0.01)
    seen = []
    half = threading.Event()

    def downloader(spec, target, token):
        # HF writes into <local_dir>/.cache/huggingface/download/*.incomplete
        tmp = Path(target) / ".cache" / "huggingface" / "download" / "x.incomplete"
        tmp.parent.mkdir(parents=True)
        tmp.write_bytes(b"w" * 5)
        assert half.wait(5)
        FakeDownloader()(spec, target, token)

    def progress(done, total):
        seen.append((done, total))
        if done == 5:
            half.set()

    store = ms.ModelStore(tmp_path, downloader=downloader)
    total = sum(len(b) for b in FILES.values())
    store.ensure(make_spec(), download=True, progress=progress)
    assert (5, total) in seen
    assert all(t == total and 0 <= d <= total for d, t in seen)
    # already provisioned: no download, no progress
    seen.clear()
    store.ensure(make_spec(), download=True, progress=progress)
    assert seen == []


def make_archive(tmp_path, files=FILES, root="vosk-model-x"):
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, data in files.items():
            zf.writestr(f"{root}/{path}", data)
        zf.writestr("../evil.txt", b"never extracted")
    blob = buf.getvalue()
    archive = tmp_path / "model.zip"
    archive.write_bytes(blob)
    spec = ModelSpec(
        alias="vosk-x",
        service="vosk",
        repo_id=archive.as_uri(),
        revision=hashlib.sha256(blob).hexdigest(),
        license="Apache-2.0",
        files=tuple(
            FileSpec(p, len(b), hashlib.sha256(b).hexdigest()) for p, b in files.items()
        ),
        archive=ArchiveSource(root=root, size=len(blob)),
    )
    return spec


def test_archive_model_is_downloaded_verified_and_extracted(tmp_path):
    spec = make_archive(tmp_path)
    store = ms.ModelStore(tmp_path / "store")  # default downloader dispatches
    path = store.ensure(spec, download=True, full_verify=True)
    assert (path / "model.bin").read_bytes() == b"weights-123"
    assert (path / "sub/config.json").read_bytes() == b'{"a": 1}'
    assert not (path / ".archive.zip").exists()
    assert not (tmp_path / "store" / "rt" / "evil.txt").exists()
    manifest = json.loads((path / ms.MANIFEST).read_text())
    assert manifest["revision"] == spec.revision
    assert store.verify(spec, full=True) == []


def test_archive_with_wrong_sha256_is_rejected(tmp_path):
    import dataclasses

    spec = dataclasses.replace(make_archive(tmp_path), revision="0" * 64)
    store = ms.ModelStore(tmp_path / "store")
    with pytest.raises(ms.ModelNotProvisioned, match="archive size/sha256 mismatch"):
        store.ensure(spec, download=True)
    assert not store.model_path("vosk-x").exists()
