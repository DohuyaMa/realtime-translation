"""python -m src.services.tts (entry point rt-tts)."""

import os
import sys
from typing import cast

from ..common.serve import run_service
from .app import create_app
from .settings import TtsSettings


def main() -> int:
    return run_service(
        TtsSettings.from_env,
        lambda settings: create_app(
            cast(TtsSettings, settings), bootstrap_env=os.environ
        ),
    )


if __name__ == "__main__":
    sys.exit(main())
