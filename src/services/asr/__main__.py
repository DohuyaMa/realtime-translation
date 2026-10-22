"""python -m src.services.asr (entry point rt-asr)."""

import sys
from typing import cast

from ..common.serve import run_service
from .app import create_app
from .settings import AsrSettings


def main() -> int:
    return run_service(
        AsrSettings.from_env, lambda settings: create_app(cast(AsrSettings, settings))
    )


if __name__ == "__main__":
    sys.exit(main())
