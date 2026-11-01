"""python -m src.services.phonetic (entry point rt-phonetic)."""

import os
import sys
from typing import cast

from ..common.serve import run_service
from .app import create_app
from .settings import PhoneticSettings


def main() -> int:
    return run_service(
        PhoneticSettings.from_env,
        lambda settings: create_app(
            cast(PhoneticSettings, settings), bootstrap_env=os.environ
        ),
    )


if __name__ == "__main__":
    sys.exit(main())
