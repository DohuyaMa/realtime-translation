"""python -m src.services.vosk (entry point rt-vosk)."""

import os
import sys
from typing import cast

from ..common.serve import run_service
from .app import create_app
from .settings import VoskSettings


def main() -> int:
    return run_service(
        VoskSettings.from_env,
        lambda settings: create_app(
            cast(VoskSettings, settings), bootstrap_env=os.environ
        ),
    )


if __name__ == "__main__":
    sys.exit(main())
