"""Status panel for the remote services: ASR, LLM worker and TTS (serving node)."""

from html import escape
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ...clients.model_status import ModelStatus
from .model_state import ERROR_STYLE, format_memory, format_model_html

SERVICES = {
    "asr": "ASR (rt-asr)",
    "llm": "Переклад (LLM)",
    "tts": "TTS (rt-tts)",
}

_DOT = "font-size: 16px; font-weight: bold; color: {};"
AUTO = "auto (failover)"


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


def _entry_status(entry: Dict) -> ModelStatus:
    memory = entry.get("memory") or {}
    download = entry.get("download") or {}
    return ModelStatus(
        model_id=entry.get("model"),
        state=entry.get("state") or "unknown",
        error=entry.get("model_error"),
        download_done_mb=download.get("done_mb"),
        download_total_mb=download.get("total_mb"),
        device=entry.get("device"),
        rss_mb=memory.get("rss_mb"),
        swap_mb=memory.get("swap_mb"),
        vram_mb=memory.get("vram_mb"),
        ready=bool(entry.get("ready")),
    )


def format_instances(entries: List[Dict], nodes: Dict[str, str]) -> str:
    """Rich text, one line per rt-asr/rt-tts instance: its ACTUAL model and
    state (download progress, error in red) and memory."""
    lines = []
    for e in entries:
        url = e.get("url") or "?"
        name = escape(nodes.get(url) or url)
        if not e.get("connected"):
            lines.append(
                f"{name}: "
                + f'<span style="{ERROR_STYLE}">'
                + escape(f"недоступний ({e.get('error') or 'unreachable'})")
                + "</span>"
            )
            continue
        ms = _entry_status(e)
        lines.append(f"{name}: {format_model_html(ms)}; {escape(format_memory(ms))}")
    return "<br>".join(lines)


# get_stats() counters shown under the services: key -> label
PIPELINE_COUNTERS = {
    "llm_rejected": "відкинуто LLM-відповідей (не переклад)",
    "asr_no_words": "ASR без слів",
    "pipeline_errors": "помилок сервісів",
    "dropped_segments": "відкинуто фраз",
}


def format_pipeline_stats(status: Dict) -> str:
    """Last phrase's per-stage latency, queue depths, backpressure and
    problem counters (rejected translations, ASR output without words...)."""
    t = status.get("timing") or {}
    parts = []
    if t:

        def ms(key):
            v = t.get(key)
            return "-" if v is None else f"{v:.0f}"

        parts.append(
            f"Фраза #{t.get('seq')}: ASR {ms('asr_ms')} мс · LLM {ms('llm_ms')} мс"
            f" (усього {ms('llm_total_ms')}) · TTS {ms('tts_ms')} мс ·"
            f" кінець мовлення → звук {ms('e2e_ms')} мс"
        )
    queues = status.get("queues") or {}
    if any(queues.values()):
        parts.append("черги: " + ", ".join(f"{k} {v}" for k, v in queues.items() if v))
    behind = {k: v for k, v in (status.get("backpressure") or {}).items() if v}
    if behind:
        parts.append("не встигає: " + ", ".join(f"{k} ×{v}" for k, v in behind.items()))
    counters = [
        f"{label}: {status[key]}"
        for key, label in PIPELINE_COUNTERS.items()
        if status.get(key)
    ]
    if counters:
        parts.append(", ".join(counters))
    return "; ".join(parts)


class ServiceStatusPanel(QGroupBox):
    """Displays aggregated status of asr/llm/tts (no process control)."""

    refresh_requested = Signal()
    endpoints_requested = Signal()
    # kind, node (None = auto): the user picked which instance serves kind
    use_changed = Signal(str, object)

    def __init__(self, parent=None):
        super().__init__("Сервіси", parent)
        self._dots: Dict[str, QLabel] = {}
        self._texts: Dict[str, QLabel] = {}
        self._details: Dict[str, QLabel] = {}
        self._use: Dict[str, QComboBox] = {}
        self._use_sig: Dict[str, tuple] = {}
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
            use = QComboBox()
            use.addItem(AUTO, None)
            use.setToolTip(
                "Який екземпляр використовувати: auto - локальний першим з"
                " failover; вузол - лише він (недоступний = помилка)"
            )
            use.activated.connect(
                lambda _i, k=key, box=use: self.use_changed.emit(k, box.currentData())
            )
            grid.addWidget(use, row, 3)
            detail = QLabel("")
            detail.setWordWrap(True)
            detail.setTextFormat(Qt.TextFormat.RichText)
            grid.addWidget(detail, row, 4)
            self._dots[key], self._texts[key], self._details[key] = dot, text, detail
            self._use[key] = use

        self._timing = QLabel("")
        self._timing.setWordWrap(True)
        self._timing.setStyleSheet("color: gray;")
        outer.addWidget(self._timing)
        bar = QHBoxLayout()
        outer.addLayout(bar)
        refresh = QPushButton("Оновити")
        refresh.clicked.connect(self.refresh_requested.emit)
        bar.addWidget(refresh)
        endpoints = QPushButton("Налаштування…")
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

    def _update_use(self, kind: str, items: List[Dict], pinned) -> None:
        sig = (tuple((i["node"], i["url"], i.get("local")) for i in items), pinned)
        if self._use_sig.get(kind) == sig:
            return
        self._use_sig[kind] = sig
        box = self._use[kind]
        box.blockSignals(True)
        box.clear()
        box.addItem(AUTO, None)
        for i in items:
            label = f"{i['node']} ({i['url']})" + (
                ", локальний" if i.get("local") else ""
            )
            box.addItem(label, i["node"])
        if pinned is not None and box.findData(pinned) < 0:
            box.addItem(f"{pinned} (недоступний)", pinned)
        box.setCurrentIndex(max(box.findData(pinned), 0) if pinned else 0)
        box.blockSignals(False)

    def update_status(self, status: Dict):
        """Apply a status dict (get_status() plus optional ``health``)."""
        self._timing.setText(format_pipeline_stats(status))
        choices = status.get("choices") or {}
        use = status.get("use") or {}
        for kind in SERVICES:
            if kind in choices:
                self._update_use(kind, choices[kind], use.get(kind))
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
            nodes = {i["url"]: i["node"] for i in choices.get(key) or ()}
            detail = escape(format_node(active.get(key)) + suffix)
            instances = format_instances(entries, nodes)
            if instances:
                detail = f"{detail}<br>{instances}" if detail else instances
            self._set(key, ok, _aggregate(entries), detail)
        llm = (health.get("llm") or [None])[0]
        session = status.get("session")
        has_session = bool(session and session.get("active"))
        ok = bool(llm and llm.get("ready")) or has_session
        text = "сесія" if has_session else ("готовий" if ok else "Offline")
        self._set("llm", ok, text, escape(format_llm_detail(llm, session)))
