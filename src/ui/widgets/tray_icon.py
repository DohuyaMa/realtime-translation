"""App state for the tray icon / main window and the translator icon.

The icon is drawn here with QPainter - two speech bubbles "A" and "Я" - not
taken from the icon theme: the theme's microphone was confused with the
system microphone indicator (owner 2026-10-31). Colour = base state
(stopped / idle grey, running coloured), a badge = loading (amber) or
error (red)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap

APP_NAME = "RT Перекладач"

# base state -> (back bubble, front bubble) fill colours
_COLOURS = {
    "stopped": ("#8a8f96", "#a7acb3"),
    "idle": ("#7d8794", "#9aa6b4"),
    "running": ("#1e6fd9", "#16a34a"),
}
_BADGES = {"loading": "#f59e0b", "error": "#dc2626"}


@dataclass(frozen=True)
class AppState:
    base: str  # stopped | idle | running
    badge: Optional[str]  # None | loading | error
    text: str  # Ukrainian, for the tooltip and the main window

    @property
    def tooltip(self) -> str:
        return f"{APP_NAME} — {self.text}"


def app_state(
    *,
    standby: bool,
    busy: Optional[str],
    translating: bool,
    errors: Sequence[str] = (),
) -> AppState:
    """``busy``: "stopping" / "starting" while the kill switch works;
    ``errors``: what the last kill-switch run reported."""
    base = "stopped" if standby else ("running" if translating else "idle")
    if busy == "stopping":
        return AppState(base, "loading", "зупиняю все й вивантажую моделі…")
    if busy == "starting":
        return AppState("stopped", "loading", "запускаю: завантажую моделі…")
    detail = "; ".join(errors)
    if standby:
        if errors:
            return AppState(base, "error", f"зупинено з помилками: {detail}")
        return AppState(base, None, "зупинено, моделі вивантажено — система вільна")
    if errors:
        return AppState(base, "error", f"запущено з помилками: {detail}")
    if translating:
        return AppState(base, None, "перекладає")
    return AppState(base, None, "готовий (переклад не запущено)")


def _bubble(x: float, y: float, w: float, h: float, tail_left: bool) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(QRectF(x, y, w, h), 7, 7)
    tail = QPainterPath()
    bx = x + (9 if tail_left else w - 9)
    dx = -6 if tail_left else 6
    tail.moveTo(QPointF(bx - 5, y + h - 1))
    tail.lineTo(QPointF(bx + dx, y + h + 8))
    tail.lineTo(QPointF(bx + 5, y + h - 1))
    tail.closeSubpath()
    return path.united(tail)


def render_icon(state: AppState, size: int = 64) -> QPixmap:
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.scale(size / 64.0, size / 64.0)
    back, front = _COLOURS[state.base]
    font = QFont()
    font.setBold(True)
    font.setPixelSize(21)
    p.setFont(font)
    for colour, rect, tail_left, letter in (
        (back, (2, 4, 36, 28), True, "A"),
        (front, (24, 24, 38, 28), False, "Я"),
    ):
        path = _bubble(*rect, tail_left)
        p.setPen(QPen(QColor("#1f2329"), 2))
        p.setBrush(QColor(colour))
        p.drawPath(path)
        p.setPen(QColor("white"))
        p.drawText(QRectF(*rect), Qt.AlignmentFlag.AlignCenter, letter)
    if state.badge is not None:
        p.setPen(QPen(QColor("white"), 3))
        p.setBrush(QColor(_BADGES[state.badge]))
        p.drawEllipse(QRectF(44, 1, 19, 19))
    p.end()
    return pix


def make_icon(state: AppState) -> QIcon:
    icon = QIcon()
    for size in (16, 22, 24, 32, 48, 64):
        icon.addPixmap(render_icon(state, size))
    return icon
