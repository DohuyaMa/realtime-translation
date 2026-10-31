# src/services/common/model_store.py
"""Model provisioning into <RT_MODEL_DIR>/rt/<alias> (spec §4.7)."""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import shutil
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Mapping, Optional

from .hf_env import configure_hf_home, read_token_file, set_offline, set_online
from .model_registry import ModelSpec, get_spec

log = logging.getLogger(__name__)

MANIFEST = ".rt-manifest.json"
Downloader = Callable[[ModelSpec, Path, Optional[str]], None]
Problem = tuple[str, str]  # (kind: "missing" | "mismatch", message)
Progress = Callable[[int, int], None]  # (done_bytes, total_bytes)
# How often a running download reports the bytes on disk.
DOWNLOAD_POLL_S = 1.0

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class ModelNotProvisioned(RuntimeError):
    """Model files are absent and cannot (or may not) be downloaded."""


class ModelIntegrityError(RuntimeError):
    """Model files do not match the pinned size/sha256."""


def parse_bool(value: str, name: str) -> bool:
    lowered = value.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ValueError(f"{name} must be a boolean (true/false), got {value!r}")


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def hf_downloader(spec: ModelSpec, target_dir: Path, token: str | None) -> None:
    from huggingface_hub import snapshot_download  # lazy: HF_HOME must be set first

    snapshot_download(
        repo_id=spec.repo_id,
        revision=spec.revision,
        local_dir=str(target_dir),
        allow_patterns=spec.allow_patterns,
        token=token,
    )


# Read size of the archive download stream.
ARCHIVE_CHUNK = 1024 * 1024


def archive_downloader(spec: ModelSpec, target_dir: Path, token: str | None) -> None:
    """Fetch the pinned zip (``spec.repo_id``), check its size + sha256
    (``spec.revision``) and extract the spec's files from ``archive.root/``.

    The archive is streamed into ``target_dir`` (the progress poll counts it)
    and removed after extraction; only the listed files are extracted, so
    archive paths never escape ``target_dir``."""
    import urllib.request
    import zipfile

    archive = spec.archive
    if archive is None:
        raise ValueError(f"{spec.alias} is not an archive model")
    zip_path = target_dir / ".archive.zip"
    digest = hashlib.sha256()
    size = 0
    with urllib.request.urlopen(spec.repo_id, timeout=60) as resp, open(
        zip_path, "wb"
    ) as out:
        while chunk := resp.read(ARCHIVE_CHUNK):
            out.write(chunk)
            digest.update(chunk)
            size += len(chunk)
    if size != archive.size or digest.hexdigest() != spec.revision:
        raise ModelIntegrityError(
            f"{spec.alias}: archive size/sha256 mismatch "
            f"(got {size}, {digest.hexdigest()})"
        )
    with zipfile.ZipFile(zip_path) as zf:
        for f in spec.files:
            dest = target_dir / f.path
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(f"{archive.root}/{f.path}") as src, open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst, ARCHIVE_CHUNK)
    zip_path.unlink()


def download_model(spec: ModelSpec, target_dir: Path, token: str | None) -> None:
    """Default downloader: the archive when the spec has one, else HF."""
    if spec.archive is not None:
        archive_downloader(spec, target_dir, token)
    else:
        hf_downloader(spec, target_dir, token)


def _bytes_under(path: Path) -> int:
    """Bytes on disk below ``path`` (the HF ``*.incomplete`` files included)."""
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.stat(os.path.join(root, name)).st_size
            except OSError:
                pass  # renamed/removed mid-walk by the downloader
    return total


@contextmanager
def _reporting(path: Path, total: int, progress: Progress | None) -> Iterator[None]:
    """Report the bytes under ``path`` every DOWNLOAD_POLL_S while running."""
    if progress is None:
        yield
        return
    stop = threading.Event()

    def poll() -> None:
        while True:
            progress(min(_bytes_under(path), total), total)
            if stop.wait(DOWNLOAD_POLL_S):
                return

    thread = threading.Thread(target=poll, name="model-download", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()


class ModelStore:
    def __init__(self, root: Path, downloader: Downloader = download_model):
        self.root = Path(root)
        self._downloader = downloader

    def model_path(self, alias: str) -> Path:
        return self.root / "rt" / alias

    @contextmanager
    def _lock(self, alias: str) -> Iterator[None]:
        lock_path = self.root / "rt" / f"{alias}.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "a+") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def verify(self, spec: ModelSpec, full: bool = False) -> list[Problem]:
        path = self.model_path(spec.alias)
        manifest_path = path / MANIFEST
        if not manifest_path.is_file():
            return [("missing", f"no manifest in {path}")]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("revision") != spec.revision:
            return [
                (
                    "mismatch",
                    f"revision {manifest.get('revision')} != pinned {spec.revision}",
                )
            ]
        problems: list[Problem] = []
        for f in spec.files:
            file_path = path / f.path
            if not file_path.is_file():
                problems.append(("missing", f"missing file {f.path}"))
            elif file_path.stat().st_size != f.size:
                problems.append(("mismatch", f"size mismatch for {f.path}"))
            elif full and sha256_file(file_path) != f.sha256:
                problems.append(("mismatch", f"sha256 mismatch for {f.path}"))
        return problems

    def ensure(
        self,
        spec: ModelSpec,
        *,
        download: bool,
        token: str | None = None,
        full_verify: bool = False,
        progress: Progress | None = None,
    ) -> Path:
        path = self.model_path(spec.alias)
        with self._lock(spec.alias):
            problems = self.verify(spec, full=full_verify)
            if not problems:
                return path
            if not download:
                raise ModelNotProvisioned(
                    f"model {spec.alias} not provisioned in {path}: {problems[0][1]}; "
                    f"run: rt-models fetch {spec.alias}"
                )
            self._install(spec, token, progress)
            return path

    def _install(
        self, spec: ModelSpec, token: str | None, progress: Progress | None = None
    ) -> None:
        final = self.model_path(spec.alias)
        partial = final.with_name(final.name + ".partial")
        if partial.exists():
            shutil.rmtree(partial)
        partial.mkdir(parents=True)
        try:
            # an archive is on disk next to its extracted files until the end
            total = sum(f.size for f in spec.files) + (
                spec.archive.size if spec.archive else 0
            )
            with _reporting(partial, total, progress):
                self._downloader(spec, partial, token)
        except Exception as exc:
            shutil.rmtree(partial, ignore_errors=True)
            raise ModelNotProvisioned(
                f"download of {spec.alias} failed: {exc}"
            ) from exc
        files: dict[str, dict] = {}
        for f in spec.files:
            file_path = partial / f.path
            if not file_path.is_file():
                shutil.rmtree(partial, ignore_errors=True)
                raise ModelIntegrityError(
                    f"{spec.alias}: {f.path} missing after download"
                )
            size = file_path.stat().st_size
            digest = sha256_file(file_path)
            if size != f.size or digest != f.sha256:
                shutil.rmtree(partial, ignore_errors=True)
                raise ModelIntegrityError(
                    f"{spec.alias}: {f.path} size/sha256 mismatch "
                    f"(got {size}, {digest})"
                )
            files[f.path] = {"size": size, "sha256": digest}
        manifest = {
            "alias": spec.alias,
            "repo_id": spec.repo_id,
            "revision": spec.revision,
            "license": spec.license,
            "files": files,
            "verified_at": int(time.time()),
        }
        (partial / MANIFEST).write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )
        if final.exists():
            shutil.rmtree(final)
        os.replace(partial, final)


def _verify_manifest_dir(path: Path) -> None:
    manifest = json.loads((path / MANIFEST).read_text(encoding="utf-8"))
    for rel, meta in manifest.get("files", {}).items():
        file_path = path / rel
        if not file_path.is_file() or file_path.stat().st_size != meta["size"]:
            raise ModelIntegrityError(
                f"{path}: {rel} missing or size differs from manifest"
            )


def resolve_model(
    value: str,
    store: ModelStore,
    *,
    download: bool,
    token: str | None = None,
    full_verify: bool = False,
    lookup: Callable[[str], ModelSpec] = get_spec,
    progress: Progress | None = None,
) -> Path:
    if os.path.isabs(value):
        path = Path(value)
        if not path.exists():
            raise ModelNotProvisioned(f"model path does not exist: {value}")
        if not path.is_dir():
            raise ModelNotProvisioned(f"model path is not a directory: {value}")
        if (path / MANIFEST).is_file():
            _verify_manifest_dir(path)
        else:
            log.warning("unmanaged model directory (no %s): %s", MANIFEST, path)
        return path
    return store.ensure(
        lookup(value),
        download=download,
        token=token,
        full_verify=full_verify,
        progress=progress,
    )


@dataclass(frozen=True)
class ModelSettings:
    model_dir: Path
    download: bool
    full_verify: bool
    token_file: str | None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ModelSettings":
        env = os.environ if env is None else env
        model_dir = Path(
            env.get("RT_MODEL_DIR") or Path.home() / ".cache" / "rt-speech" / "models"
        )
        download = parse_bool(env.get("RT_MODEL_DOWNLOAD", "true"), "RT_MODEL_DOWNLOAD")
        verify = env.get("RT_MODEL_VERIFY", "quick").strip().lower()
        if verify not in ("quick", "full"):
            raise ValueError(f"RT_MODEL_VERIFY must be quick|full, got {verify!r}")
        return cls(
            model_dir=model_dir,
            download=download,
            full_verify=verify == "full",
            token_file=env.get("RT_HF_TOKEN_FILE") or None,
        )

    def store(self) -> ModelStore:
        return ModelStore(self.model_dir)

    def token(self) -> str | None:
        return read_token_file(self.token_file)


def prepare_model(
    value: str, settings: ModelSettings, progress: Progress | None = None
) -> Path:
    """Loader entry point: HF_HOME -> resolve/provision -> HF_HUB_OFFLINE=1.

    ``progress`` gets (done, total) bytes while a download runs."""
    configure_hf_home(settings.model_dir)
    if settings.download:
        set_online()  # an earlier load left the process offline
    try:
        return resolve_model(
            value,
            settings.store(),
            download=settings.download,
            token=settings.token(),
            full_verify=settings.full_verify,
            progress=progress,
        )
    finally:
        set_offline()  # also after a failed download
