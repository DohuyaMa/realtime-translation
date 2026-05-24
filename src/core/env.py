"""Environment setup utilities for the real-time translation system."""
import os
from loguru import logger


_HF_TOKEN_FILE = os.path.expanduser("~/.config/real-time-translator/.hf_token")


def setup_ml_env():
    """Set up machine learning environment variables.

    This function sets up environment variables for HuggingFace and other ML libraries.
    It's designed to work with Nix environments where these variables might be set
    externally, but provides fallbacks for standalone usage.

    HuggingFace token: set HF_TOKEN env var, or write the token to
    ~/.config/real-time-translator/.hf_token (one line, no trailing whitespace).
    """
    os.environ.setdefault("HF_HOME", os.path.expanduser("~/real-time-translator-cache/huggingface"))
    os.environ.setdefault("TRANSFORMERS_CACHE", os.path.expanduser("~/real-time-translator-cache/transformers"))
    os.environ.setdefault("HF_HUB_CACHE", os.path.expanduser("~/real-time-translator-cache/huggingface/hub"))

    if not os.environ.get("HF_TOKEN") and os.path.isfile(_HF_TOKEN_FILE):
        try:
            token = open(_HF_TOKEN_FILE).read().strip()
            if token:
                os.environ["HF_TOKEN"] = token
                os.environ["HUGGING_FACE_HUB_TOKEN"] = token
                logger.info(f"HF_TOKEN loaded from {_HF_TOKEN_FILE}")
        except OSError:
            pass

    logger.info(f"HF_HOME set to: {os.environ.get('HF_HOME')}")
    logger.info(f"TRANSFORMERS_CACHE set to: {os.environ.get('TRANSFORMERS_CACHE')}")
    logger.info(f"HF_HUB_CACHE set to: {os.environ.get('HF_HUB_CACHE')}")


def setup_audio_env():
    """Set up audio-related environment variables."""
    # Set environment variables for audio processing
    os.environ.setdefault("PULSE_RUNTIME_PATH", "/run/user/$(id -u)/pulse")
    os.environ.setdefault("SDL_AUDIODRIVER", "pulse")
    
    logger.debug("Audio environment variables configured")


def setup_env():
    """Set up all environment variables for the application."""
    setup_ml_env()
    setup_audio_env()