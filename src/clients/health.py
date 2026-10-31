"""Background health polling so the Qt thread never does network I/O."""

from __future__ import annotations

import os
import threading
from typing import Any, Dict, List, Optional

from loguru import logger

from .llm import LlmClient
from .model_status import parse_status
from .pool import SpeechPools
from .session import WorkerSessions


def _entry(service: str, url: Optional[str], **kw: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "service": service,
        "url": url,
        "connected": False,
        "ready": False,
        "pending": None,
        "model": None,
        "state": None,
        "model_error": None,
        "download": None,
        "memory": None,
        "device": None,
        "error": "unreachable",
    }
    base.update(kw)
    return base


def _from_status(service: str, url: str, status: dict) -> Dict[str, Any]:
    """Entry from rt-asr/rt-tts /status: the ACTUAL model and its state."""
    ms = parse_status(status)
    pending = status.get("pending")
    download = (
        {"done_mb": ms.download_done_mb, "total_mb": ms.download_total_mb}
        if ms.download_total_mb is not None
        else None
    )
    memory = {"rss_mb": ms.rss_mb, "swap_mb": ms.swap_mb, "vram_mb": ms.vram_mb}
    return _entry(
        service,
        url,
        connected=True,
        ready=ms.ready,
        pending=pending if isinstance(pending, int) else None,
        model=ms.model_id,
        state=ms.state,
        model_error=ms.error,
        download=download,
        memory=memory if any(v is not None for v in memory.values()) else None,
        device=ms.device,
        error=None,
    )


class ServiceHealthMonitor:
    def __init__(
        self,
        pools: SpeechPools,
        llm: Optional[LlmClient],
        sessions: Optional[WorkerSessions],
        interval_s: float = 5,
    ) -> None:
        self._pools = pools
        self._llm = llm
        self._sessions = sessions
        self._interval = interval_s
        self._lock = threading.Lock()
        self._snapshot: Dict[str, List[Dict[str, Any]]] = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._snapshot = self._initial()

    def _initial(self) -> Dict[str, List[Dict[str, Any]]]:
        snap: Dict[str, List[Dict[str, Any]]] = {
            name: [_entry(name, u) for u in pool.urls]
            for name, pool in self._pools.by_name().items()
        }
        if self._llm is not None:
            snap["llm"] = [self._llm_entry(None)]
        return snap

    def _probe_pool(self, name: str, pool: Any) -> List[Dict[str, Any]]:
        entries = []
        for url, client in pool.clients().items():
            try:
                status = client.status()
            except Exception as e:  # a broken client must not kill the poller
                logger.debug("health probe failed: {}", e)
                status = None
            if status is None:
                entry = _entry(name, url)
                pool.update_status(url, ready=False, pending=None)
            else:
                entry = _from_status(name, url, status)
                pool.update_status(url, ready=entry["ready"], pending=entry["pending"])
            entries.append(entry)
        return entries

    def _llm_entry(
        self, status: Optional[dict], error: Optional[str] = None
    ) -> Dict[str, Any]:
        session = self._sessions.snapshot() if self._sessions is not None else None
        url = session["active"] if session else None
        if url is None and self._llm is not None:
            try:
                url = self._llm.base_url
            except Exception:
                url = None
        entry = _entry("llm", url)
        if status is not None:
            model = status.get("model")
            model = model if isinstance(model, dict) else {}
            path = model.get("path")
            entry.update(
                connected=True,
                ready=bool(model.get("loaded")),
                model=os.path.basename(path) if path else None,
                error=None,
            )
        elif error:
            entry["error"] = error
        entry["session"] = session
        return entry

    def _probe_llm(self) -> List[Dict[str, Any]]:
        status: Optional[dict] = None
        error: Optional[str] = None
        try:
            status = self._llm.status()  # type: ignore[union-attr]
        except Exception as e:
            logger.debug("llm status failed: {}", e)
            error = getattr(e, "kind", None) or "unreachable"
        return [self._llm_entry(status, error)]

    def poll_once(self) -> Dict[str, List[Dict[str, Any]]]:
        snap: Dict[str, List[Dict[str, Any]]] = {}
        for name, pool in self._pools.by_name().items():
            snap[name] = self._probe_pool(name, pool)
        if self._llm is not None:
            snap["llm"] = self._probe_llm()
        with self._lock:
            self._snapshot = snap
        return self.snapshot()

    def snapshot(self) -> Dict[str, List[Dict[str, Any]]]:
        with self._lock:
            return {k: [dict(e) for e in v] for k, v in self._snapshot.items()}

    def _run(self) -> None:
        self.poll_once()
        while not self._stop.wait(self._interval):
            self.poll_once()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="rt-health")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
