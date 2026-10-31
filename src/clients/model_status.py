"""Typed view of an rt-asr / rt-tts ``GET /status``: the model that is
ACTUALLY in the service (not the configured one), its state, download
progress, error, and the process memory."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

STATES = ("unloaded", "downloading", "loading", "ready", "error")
TRANSITIONAL = frozenset({"downloading", "loading"})


def _num(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) else None


@dataclass(frozen=True)
class ModelStatus:
    model_id: Optional[str]
    state: str  # one of STATES (an unknown value from the service is kept)
    error: Optional[str] = None
    download_done_mb: Optional[float] = None
    download_total_mb: Optional[float] = None
    device: Optional[str] = None
    rss_mb: Optional[float] = None
    swap_mb: Optional[float] = None
    vram_mb: Optional[float] = None
    ready: bool = False
    # rt-asr: output segments dropped by the service, per reason
    # (e.g. {"hallucination": 3}); empty for rt-tts
    dropped: Dict[str, int] = field(default_factory=dict)

    @property
    def transitional(self) -> bool:
        return self.state in TRANSITIONAL

    @property
    def download_percent(self) -> Optional[int]:
        done, total = self.download_done_mb, self.download_total_mb
        if done is None or not total:
            return None
        return max(0, min(100, int(done * 100 / total)))


def parse_status(status: Mapping[str, Any]) -> ModelStatus:
    model = status.get("model")
    model = model if isinstance(model, Mapping) else {}
    download = model.get("download")
    download = download if isinstance(download, Mapping) else {}
    memory = status.get("memory")
    memory = memory if isinstance(memory, Mapping) else {}
    state = str(model.get("state") or "unknown")
    error = model.get("error")
    dropped = status.get("dropped_segments")
    dropped = dropped if isinstance(dropped, Mapping) else {}
    return ModelStatus(
        model_id=model.get("id"),
        state=state,
        error=str(error) if error else None,
        download_done_mb=_num(download.get("done_mb")),
        download_total_mb=_num(download.get("total_mb")),
        device=model.get("device") or status.get("device"),
        rss_mb=_num(memory.get("rss_mb")),
        swap_mb=_num(memory.get("swap_mb")),
        vram_mb=_num(memory.get("vram_mb")),
        ready=bool(status.get("ready", state == "ready")),
        dropped={str(k): int(v) for k, v in dropped.items() if v},
    )
