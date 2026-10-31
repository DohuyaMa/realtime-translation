"""Status panel for the remote services: ASR, LLM (lease) and TTS."""

from typing import Dict, List, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

SERVICES = {
    "asr": "ASR (rt-asr)",
    "llm": "Переклад (LLM)",
    "tts": "TTS (rt-tts)",
}

_DOT = "font-size: 16px; font-weight: bold; color: {};"


def _aggregate(entries: List[Dict]) -> str:
    if not entries:
        return "не налаштовано"
    ready = sum(1 for e in entries if e.get("ready"))
    return f"{ready}/{len(entries)} готові"


def format_llm_detail(entry: Optional[Dict], lease: Optional[Dict], source: str):
    """Text for the LLM row: worker, model, expires_at and source."""
    entry = entry or {}
    lease = lease or entry.get("lease") or {}
    if lease.get("mode") == "no_lease":
        source = f"без lease ({lease.get('no_lease_source')})"
    parts = [f"джерело: {source}"]
    if lease.get("service"):
        parts.append(f"воркер: {lease['service']}")
    model = lease.get("model") or entry.get("model")
    if model:
        parts.append(f"модель: {model}")
    if lease.get("expires_at"):
        parts.append(f"lease до: {lease['expires_at']}")
    if lease.get("skipped"):
        parts.append(f"недоступні Django: {', '.join(lease['skipped'])}")
    if lease.get("error"):
        parts.append(f"помилка: {lease['error']}")
    return ", ".join(parts)


class ServiceStatusPanel(QGroupBox):
    """Displays aggregated status of asr/llm/tts (no process control)."""

    refresh_requested = Signal()
    endpoints_requested = Signal()

    def __init__(self, parent=None):
        super().__init__("Сервіси", parent)
        self._dots: Dict[str, QLabel] = {}
        self._texts: Dict[str, QLabel] = {}
        self._details: Dict[str, QLabel] = {}
        self.llm_source: Optional[str] = None  # lease | direct | none
        self._build()

    def _build(self):
        outer = QVBoxLayout(self)
        grid = QGridLayout()
        outer.addLayout(grid)
        for row, (key, label) in enumerate(SERVICES.items()):
            dot = QLabel("●")
            dot.setStyleSheet(_DOT.format("red"))
            grid.addWidget(dot, row, 0)
            grid.addWidget(QLabel(label), row, 1)
            text = QLabel("Offline")
            text.setStyleSheet("color: gray;")
            grid.addWidget(text, row, 2)
            detail = QLabel("")
            detail.setWordWrap(True)
            grid.addWidget(detail, row, 3)
            self._dots[key], self._texts[key], self._details[key] = dot, text, detail

        bar = QHBoxLayout()
        outer.addLayout(bar)
        refresh = QPushButton("Оновити")
        refresh.clicked.connect(self.refresh_requested.emit)
        bar.addWidget(refresh)
        endpoints = QPushButton("Ендпоінти…")
        endpoints.clicked.connect(self.endpoints_requested.emit)
        bar.addWidget(endpoints)
        bar.addStretch()

    def get_service_names(self) -> List[str]:
        return list(SERVICES)

    def _set(self, key: str, ok: bool, text: str, detail: str = ""):
        self._dots[key].setStyleSheet(_DOT.format("green" if ok else "red"))
        self._texts[key].setText(text)
        self._texts[key].setStyleSheet("color: green;" if ok else "color: gray;")
        self._details[key].setText(detail)

    def update_status(self, status: Dict):
        """Apply a status dict (get_status() plus optional ``health``)."""
        health = status.get("health") or {}
        pinned = status.get("pinned") or {}
        for key in ("asr", "tts"):
            entries = health.get(key) or []
            ok = any(e.get("ready") for e in entries)
            detail = f"закріплено: {pinned.get(key)}" if pinned.get(key) else ""
            self._set(key, ok, _aggregate(entries), detail)
        llm = (health.get("llm") or [None])[0]
        lease = status.get("lease")
        source = self.llm_source or (
            "lease" if (lease or (llm or {}).get("lease")) else "direct"
        )
        detail = format_llm_detail(llm, lease, source)
        if (lease or {}).get("mode") == "no_lease":  # explicit, never green
            self._set("llm", False, "без lease", detail)
            return
        ok = bool(llm and llm.get("ready"))
        has_lease = bool(lease and lease.get("service"))
        text = "готовий" if ok else ("lease" if has_lease else "Offline")
        self._set("llm", ok or has_lease, text, detail)
