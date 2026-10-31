"""Rich-text lines for the ACTUAL model of an rt-asr / rt-tts instance:
model id, state (download progress, error in red), memory. Shared by the
status panel and the ASR/TTS settings tabs."""

from __future__ import annotations

from html import escape
from typing import Optional

from ...clients.model_status import ModelStatus

ERROR_STYLE = "color: #c00000;"
WARN_STYLE = "color: #b36b00;"

STATE_LABELS = {
    "unloaded": "не завантажена",
    "downloading": "завантажується з мережі",
    "loading": "завантажується в пам'ять",
    "ready": "готова",
    "error": "помилка",
}


def _span(style: str, text: str) -> str:
    return f'<span style="{style}">{escape(text)}</span>'


def _size(mb: Optional[float]) -> str:
    if mb is None:
        return "—"
    return f"{mb / 1024:.1f} ГБ" if mb >= 1024 else f"{mb:.0f} МБ"


def format_state(ms: ModelStatus) -> str:
    """'готова (cuda)', 'завантажується з мережі 1200/3000 МБ (40%)', ..."""
    label = STATE_LABELS.get(ms.state, ms.state)
    if ms.state == "downloading" and ms.download_total_mb:
        label += (
            f" {ms.download_done_mb or 0:.0f}/{ms.download_total_mb:.0f} МБ"
            f" ({ms.download_percent}%)"
        )
    if ms.device and ms.state == "ready":
        label += f" ({ms.device})"
    return label


def format_model_html(ms: ModelStatus, configured: Optional[str] = None) -> str:
    """Actual model + state; error text in red; configured != actual flagged."""
    model = escape(ms.model_id or "—")
    state = format_state(ms)
    style = ERROR_STYLE if ms.state == "error" else ""
    text = f"модель: <b>{model}</b> — " + (_span(style, state) if style else state)
    if ms.error:
        text += "<br>" + _span(ERROR_STYLE, f"помилка: {ms.error}")
    if configured and configured != ms.model_id:
        text += "<br>" + _span(
            WARN_STYLE,
            f"у налаштуваннях: {configured}, але фактично в сервісі:"
            f" {ms.model_id or 'жодної'}",
        )
    return text


def format_memory(ms: ModelStatus) -> str:
    """'RAM 2.8 ГБ · swap 5.7 ГБ · VRAM 1.6 ГБ' (process of the service)."""
    if ms.rss_mb is None and ms.swap_mb is None and ms.vram_mb is None:
        return "пам'ять: сервіс не повідомляє"
    vram = "немає" if ms.vram_mb is None else _size(ms.vram_mb)
    return f"RAM {_size(ms.rss_mb)} · swap {_size(ms.swap_mb)} · VRAM {vram}"


def format_dropped(ms: ModelStatus) -> str:
    """'відкинуто сервісом: hallucination 3' (rt-asr output filter)."""
    if not ms.dropped:
        return ""
    return "відкинуто сервісом: " + ", ".join(
        f"{reason} {n}" for reason, n in sorted(ms.dropped.items())
    )
