# src/services/common/hf_env.py
"""Hugging Face environment handling (spec §4.7).

huggingface_hub reads HF_HOME / HF_HUB_OFFLINE at import time, so configure_hf_home
must run before any engine imports faster_whisper / kokoro / huggingface_hub.
set_offline additionally patches an already-imported huggingface_hub.constants
(our own downloader may have imported it during provisioning).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def configure_hf_home(model_dir: Path) -> Path:
    hf_home = Path(model_dir) / "hf"
    hf_home.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(hf_home)
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    return hf_home


def set_offline() -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    hub_constants = sys.modules.get("huggingface_hub.constants")
    if hub_constants is not None:
        hub_constants.HF_HUB_OFFLINE = True


def read_token_file(path: str | None) -> str | None:
    if not path:
        return None
    token_path = Path(path)
    if not token_path.is_file():
        raise FileNotFoundError(f"RT_HF_TOKEN_FILE not found: {path}")
    token = token_path.read_text(encoding="utf-8").strip()
    if not token:
        raise ValueError(f"RT_HF_TOKEN_FILE is empty: {path}")
    return token
