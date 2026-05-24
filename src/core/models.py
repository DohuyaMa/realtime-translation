"""Model status tracking and download management."""
import os
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Callable, List, Optional

from loguru import logger

from .huggingface import download_model


class ModelStatus(Enum):
    CACHED = auto()
    MISSING = auto()
    DOWNLOADING = auto()
    ERROR = auto()
    NIX_MANAGED = auto()
    NOT_REQUIRED = auto()


@dataclass
class ModelSpec:
    model_id: str
    display_name: str
    type: str  # "huggingface", "faster-whisper", "spacy-nix"
    cache_path: str
    nix_managed: bool = False
    size_hint: str = ""


@dataclass
class ModelInfo:
    spec: ModelSpec
    status: ModelStatus
    size_bytes: Optional[int] = None
    error: str = ""


_WHISPER_MODELS = ["tiny", "base", "small", "medium", "large"]

_WHISPER_HF_REPO = {size: f"Systran/faster-whisper-{size}" for size in _WHISPER_MODELS}
_WHISPER_HF_REPO["large"] = "Systran/faster-whisper-large-v3"

_REGISTRY: dict[str, ModelSpec] = {
    "translate": ModelSpec(
        model_id="Helsinki-NLP/opus-mt-uk-en",
        display_name="Translation (Helsinki opus-mt-uk-en)",
        type="huggingface",
        cache_path=str(Path.home() / "real-time-translator-cache" / "huggingface" / "hub" / "models--Helsinki-NLP--opus-mt-uk-en"),
        size_hint="~300 MB",
    ),
    "tts": ModelSpec(
        model_id="hexgrad/Kokoro-82M",
        display_name="TTS Kokoro-82M",
        type="huggingface",
        cache_path=str(Path.home() / ".cache" / "huggingface" / "hub" / "models--hexgrad--Kokoro-82M"),
        size_hint="~330 MB",
    ),
    "en_core_web_sm": ModelSpec(
        model_id="en_core_web_sm",
        display_name="spaCy en_core_web_sm (Nix)",
        type="spacy-nix",
        cache_path="",
        nix_managed=True,
        size_hint="~50 MB",
    ),
}


def _whisper_spec(model_name: str) -> ModelSpec:
    return ModelSpec(
        model_id=model_name,
        display_name=f"Whisper {model_name}",
        type="faster-whisper",
        cache_path=str(Path.home() / ".cache" / "whisper" / model_name),
        size_hint="",
    )


def _dir_size(path: str) -> Optional[int]:
    p = Path(path)
    if not p.exists():
        return None
    try:
        result = subprocess.run(
            ["du", "-sb", path], capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return int(result.stdout.split()[0])
    except Exception:
        pass
    return None


def _fmt_size(size_bytes: Optional[int]) -> str:
    if size_bytes is None:
        return ""
    for unit, threshold in [("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)]:
        if size_bytes >= threshold:
            return f"{size_bytes / threshold:.1f} {unit}"
    return f"{size_bytes} B"


class ModelManager:
    """Check status and download AI models."""

    def get_info(self, model_id: str, whisper_model: str = "small") -> ModelInfo:
        if model_id == "whisper":
            spec = _whisper_spec(whisper_model)
        else:
            spec = _REGISTRY.get(model_id)
            if spec is None:
                return ModelInfo(
                    spec=ModelSpec(model_id, model_id, "unknown", ""),
                    status=ModelStatus.ERROR,
                    error="Unknown model",
                )

        if spec.nix_managed:
            return ModelInfo(spec=spec, status=ModelStatus.NIX_MANAGED)

        exists = Path(spec.cache_path).exists()
        size = _dir_size(spec.cache_path) if exists else None
        return ModelInfo(
            spec=spec,
            status=ModelStatus.CACHED if exists else ModelStatus.MISSING,
            size_bytes=size,
        )

    def whisper_not_required(self) -> ModelInfo:
        spec = ModelSpec(
            model_id="wyoming",
            display_name="Whisper (Wyoming remote)",
            type="wyoming",
            cache_path="",
        )
        return ModelInfo(spec=spec, status=ModelStatus.NOT_REQUIRED)

    def download(
        self,
        model_id: str,
        whisper_model: str = "small",
        progress_cb: Optional[Callable[[int, str], None]] = None,
    ) -> bool:
        """Download model in-process using huggingface_hub.

        Uses the huggingface_hub library directly (available in the Nix
        closure) instead of a subprocess — the old subprocess approach
        failed because sys.executable is the bare store Python without
        PYTHONPATH.
        """
        if model_id == "translate":
            cache_dir = os.environ.get(
                "HF_HUB_CACHE",
                str(Path.home() / "real-time-translator-cache" / "huggingface" / "hub"),
            )
            return download_model(
                "Helsinki-NLP/opus-mt-uk-en",
                local_dir=cache_dir,
                progress_cb=progress_cb,
            )
        elif model_id == "tts":
            return download_model(
                "hexgrad/Kokoro-82M",
                progress_cb=progress_cb,
            )
        elif model_id == "whisper":
            return self._download_whisper(whisper_model, progress_cb)
        return False

    def _download_whisper(
        self, model_name: str, progress_cb: Optional[Callable[[int, str], None]]
    ) -> bool:
        repo_id = _WHISPER_HF_REPO.get(model_name)
        if not repo_id:
            logger.error(f"Unknown whisper model: {model_name}")
            if progress_cb:
                progress_cb(-1, f"Unknown whisper model: {model_name}")
            return False

        cache = str(Path.home() / ".cache" / "whisper" / model_name)
        if progress_cb:
            progress_cb(0, f"Downloading {repo_id} to {cache}...")
        return download_model(repo_id, local_dir=cache, progress_cb=progress_cb)

    def clear_cache(self, model_id: str, whisper_model: str = "small") -> bool:
        info = self.get_info(model_id, whisper_model)
        path = info.spec.cache_path
        if not path or not Path(path).exists():
            return False
        try:
            shutil.rmtree(path)
            return True
        except Exception as e:
            logger.error(f"Failed to clear cache for {model_id}: {e}")
            return False

    @property
    def whisper_models(self) -> List[str]:
        return _WHISPER_MODELS

    def format_size(self, size_bytes: Optional[int]) -> str:
        return _fmt_size(size_bytes)

    def get_downloadable_whisper_models(self) -> list[dict]:
        """Return list of all available whisper models with metadata.

        Returns dicts with keys: id, display_name, size_hint, repo_id.
        """
        size_map = {
            "tiny": "~150 MB",
            "base": "~300 MB",
            "small": "~500 MB",
            "medium": "~1.5 GB",
            "large": "~3 GB",
        }
        return [
            {
                "id": name,
                "display_name": f"Whisper {name}",
                "size_hint": size_map.get(name, ""),
                "repo_id": _WHISPER_HF_REPO.get(name, f"Systran/faster-whisper-{name}"),
            }
            for name in _WHISPER_MODELS
        ]
