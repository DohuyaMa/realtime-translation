"""Best-effort VRAM usage of this process (CUDA only, via nvidia-smi)."""

from __future__ import annotations

import os
import subprocess
import time
from typing import Callable, Optional


class VramProbe:
    def __init__(
        self,
        runner: Callable = subprocess.run,
        clock: Callable[[], float] = time.monotonic,
        ttl: float = 5.0,
        pid: Optional[int] = None,
    ) -> None:
        self._runner = runner
        self._clock = clock
        self._ttl = ttl
        self._pid = pid if pid is not None else os.getpid()
        self._at: Optional[float] = None
        self._value: Optional[int] = None

    def read(self, device: str) -> Optional[int]:
        """MB on the GPU; None on cpu or when nvidia-smi cannot tell."""
        if device != "cuda":
            return None
        now = self._clock()
        if self._at is not None and now - self._at < self._ttl:
            return self._value
        self._at = now
        self._value = self._query()
        return self._value

    def _query(self) -> Optional[int]:
        try:
            out = self._runner(
                [
                    "nvidia-smi",
                    "--query-compute-apps=pid,used_memory",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=2,
                check=True,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        total = None
        for line in out.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) == 2 and parts[0] == str(self._pid) and parts[1].isdigit():
                total = (total or 0) + int(parts[1])
        return total
