"""HuggingFace Hub integration for model download and discovery.

Ported from system-conf/common/modules/llm/app/integrations/huggingface/.
Provides token resolution, pre-download access validation, and listing
of models available for the translation pipeline services.
"""
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# ── Known Whisper model sizes on HuggingFace (for faster-whisper) ──
WHISPER_HF_REPOS = [
    "Systran/faster-whisper-tiny",
    "Systran/faster-whisper-base",
    "Systran/faster-whisper-small",
    "Systran/faster-whisper-medium",
    "Systran/faster-whisper-large-v2",
    "Systran/faster-whisper-large-v3",
]

# ── Wyoming-compatible whisper models ──
WYOMING_WHISPER_MODELS = [
    "tiny-int8",
    "base-int8",
    "small-int8",
    "medium-int8",
    "large-v2-int8",
    "large-v3-int8",
    "tiny",
    "base",
    "small",
    "medium",
    "large-v2",
    "large-v3",
]


def get_hf_token() -> Optional[str]:
    """Resolve HF token from fallback sources (copy of swarm-nix auth.py logic).

    Resolution order:
      1. HF_TOKEN environment variable
      2. /run/secrets/hg-token  (NixOS sops-nix)
      3. ~/.cache/huggingface/token (huggingface-cli login)
    """
    # 1. Environment variable
    token = os.getenv("HF_TOKEN")
    if token:
        logger.debug("Using HF token from HF_TOKEN env var")
        return token

    # 2. NixOS secrets file
    token_file = os.environ.get("HF_TOKEN_FILE", "/run/secrets/hg-token")
    if os.path.exists(token_file):
        try:
            with open(token_file) as f:
                token = f.read().strip()
                if "=" in token:
                    token = token.split("=", 1)[1].strip()
                if token:
                    logger.debug(f"Using HF token from {token_file}")
                    return token
        except Exception as e:
            logger.debug(f"Failed to read HF token from file: {e}")

    # 3. HF cache
    cache_path = os.path.expanduser("~/.cache/huggingface/token")
    if os.path.exists(cache_path):
        try:
            with open(cache_path) as f:
                token = f.read().strip()
                if token:
                    logger.debug("Using HF token from cache file")
                    return token
        except Exception:
            pass

    logger.info("No HF token found — gated models will fail")
    return None


def download_model(
    repo_id: str,
    local_dir: Optional[str] = None,
    token: Optional[str] = None,
    progress_cb=None,
) -> bool:
    """Download a HuggingFace model snapshot in-process.

    This replaces the old subprocess-based _download_hf which broke
    because sys.executable is the bare store Python without PYTHONPATH.

    Args:
        repo_id: HF repo ID (e.g. 'Systran/faster-whisper-small')
        local_dir: Destination directory (or None for default HF cache)
        token: HF token for gated models
        progress_cb: Optional callable(percent, message)

    Returns:
        True on success, False on failure.
    """
    try:
        from huggingface_hub import snapshot_download
    except ImportError as e:
        logger.error(f"huggingface_hub not available: {e}")
        if progress_cb:
            progress_cb(-1, "huggingface_hub not installed in this environment")
        return False

    resolved_token = token or get_hf_token()

    try:
        if progress_cb:
            progress_cb(0, f"Downloading {repo_id}...")

        kwargs = dict(repo_id=repo_id)
        if local_dir:
            kwargs["local_dir"] = local_dir
            kwargs["local_dir_use_symlinks"] = False
        if resolved_token:
            kwargs["token"] = resolved_token

        path = snapshot_download(**kwargs)

        if progress_cb:
            progress_cb(100, f"Downloaded to {path}")
        logger.info(f"✓ Model {repo_id} downloaded to {path}")
        return True

    except Exception as e:
        logger.error(f"Failed to download {repo_id}: {e}")
        if progress_cb:
            progress_cb(-1, f"Error: {e}")
        return False


def list_repo_files(repo_id: str, token: Optional[str] = None) -> list[str]:
    """List files in a HF repo (useful for model discovery)."""
    try:
        from huggingface_hub import HfApi
    except ImportError:
        logger.error("huggingface_hub not available")
        return []

    resolved_token = token or get_hf_token()
    try:
        api = HfApi()
        return api.list_repo_files(repo_id, token=resolved_token)
    except Exception as e:
        logger.error(f"Failed to list repo files for {repo_id}: {e}")
        return []


def check_model_size(repo_id: str, token: Optional[str] = None) -> Optional[int]:
    """Get total model size in bytes for a repo."""
    try:
        from huggingface_hub import HfApi
    except ImportError:
        return None

    resolved_token = token or get_hf_token()
    try:
        api = HfApi()
        info = api.model_info(repo_id, token=resolved_token)
        total = sum(f.size for f in info.siblings if f.size)
        return total if total else None
    except Exception as e:
        logger.debug(f"Could not get size for {repo_id}: {e}")
        return None
