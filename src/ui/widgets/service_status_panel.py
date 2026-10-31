"""Status panel for the remote services: ASR, LLM worker and TTS (serving node)."""

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


def format_node(active: Optional[Dict]) -> str:
    """'вузол: <node> (<url>)' plus local/offline markers."""
    if not active:
        return ""
    url = active.get("url") or active.get("active")
    text = f"вузол: {active.get('node') or '?'} ({url})"
    if active.get("local"):
        text += ", локальний"
    if active.get("offline"):
        text += ", офлайн-токен"
    return text


def format_llm_detail(entry: Optional[Dict], session: Optional[Dict]) -> str:
    """Text for the LLM row: serving worker, session, model, down workers."""
    entry = entry or {}
    session = session or entry.get("session") or {}
    parts = []
    if session.get("active"):
        parts.append(format_node(session))
    if session.get("session_id"):
        parts.append(f"сесія: {session['session_id']}")
    if entry.get("model"):
        parts.append(f"модель: {entry['model']}")
    if session.get("down"):
        parts.append(f"недоступні: {', '.join(session['down'])}")
    if session.get("error"):
        parts.append(f"помилка: {session['error']}")
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
        active = status.get("active") or {}
        source = status.get("discovery")
        suffix = ""
        if source == "cache":
            suffix = "; список сервісів: кеш (Django недоступний)"
        elif source == "none":
            suffix = "; список сервісів: лише локальні"
        for key in ("asr", "tts"):
            entries = health.get(key) or []
            ok = any(e.get("ready") for e in entries)
            self._set(
                key, ok, _aggregate(entries), format_node(active.get(key)) + suffix
            )
        llm = (health.get("llm") or [None])[0]
        session = status.get("session")
        has_session = bool(session and session.get("active"))
        ok = bool(llm and llm.get("ready")) or has_session
        text = "сесія" if has_session else ("готовий" if ok else "Offline")
        self._set("llm", ok, text, format_llm_detail(llm, session))
