"""Process memory: what /status reports and how a dropped model is returned
to the OS."""

from __future__ import annotations

import ctypes
import gc
from pathlib import Path
from typing import Callable, Optional

from loguru import logger

_PROC_STATUS = Path("/proc/self/status")


def process_memory(path: Path = _PROC_STATUS) -> dict:
    """``{"rss_mb", "swap_mb"}`` of this process (``None`` where unknown)."""
    values: dict[str, Optional[float]] = {"rss_mb": None, "swap_mb": None}
    keys = {"VmRSS:": "rss_mb", "VmSwap:": "swap_mb"}
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return values
    for line in lines:
        parts = line.split()
        if len(parts) >= 2 and parts[0] in keys:
            values[keys[parts[0]]] = round(int(parts[1]) / 1024, 1)  # kB -> MB
    return values


def _glibc_malloc_trim() -> Optional[Callable[[int], int]]:
    try:
        return ctypes.CDLL(None).malloc_trim
    except (OSError, AttributeError):
        return None


_malloc_trim = _glibc_malloc_trim()
if _malloc_trim is None:
    logger.warning(
        "malloc_trim unavailable (not glibc): freed model memory stays in RSS"
    )


def release_memory() -> None:
    """Collect cycles (a failed load's traceback can pin a half-built model)
    and hand freed heap pages back to the OS: without ``malloc_trim`` glibc
    keeps a dropped model's arenas in RSS and the kernel swaps them out."""
    gc.collect()
    if _malloc_trim is not None:
        _malloc_trim(0)
