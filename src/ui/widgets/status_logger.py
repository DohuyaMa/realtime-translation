"""Status and logging component for the real-time translation system."""

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QCheckBox,
    QPushButton,
    QLabel,
    QSplitter,
    QPlainTextEdit,
    QGroupBox,
)
from PySide6.QtCore import Qt
from typing import List, Optional, Tuple
import datetime
from loguru import logger

SHOW_CHECK_TEXT = "Показувати текст Vosk"
CHECK_PREFIX = "    Vosk: "
CHECK_NONE = "(без звірки)"
CHECK_EMPTY = "(нічого не почув)"


class StatusLogger(QWidget):
    """Widget with three panels: recognized text, translated text, and system log."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._messages: List[Tuple] = []
        self._max_messages = 1000
        # (time, whisper text, Vosk text of the same audio or None)
        self._recognized: List[Tuple[str, str, Optional[str]]] = []
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # ── Top row: recognized | translated ──────────────────────────
        text_splitter = QSplitter(Qt.Orientation.Horizontal)

        rec_box = QGroupBox("Розпізнаний текст (Original)")
        rec_layout = QVBoxLayout(rec_box)
        self.recognized_view = QPlainTextEdit()
        self.recognized_view.setReadOnly(True)
        self.recognized_view.setPlaceholderText("Тут з'явиться розпізнана мова…")
        rec_layout.addWidget(self.recognized_view)
        # whisper's words not translated yet (uncommitted tail + words not in
        # a unit yet): grey, never spoken (spec 2026-10-31, owner decision 2)
        self.tail_label = QLabel("")
        self.tail_label.setWordWrap(True)
        self.tail_label.setStyleSheet("color: gray;")
        self.tail_label.setToolTip("Ще не перекладено: whisper може його уточнити")
        rec_layout.addWidget(self.tail_label)
        # Vosk's text of the same phrase under whisper's line, to compare
        self.show_check = QCheckBox(SHOW_CHECK_TEXT)
        self.show_check.setChecked(True)
        self.show_check.setToolTip(
            "Під кожною фразою whisper - що почув Vosk (звірка) у тому ж аудіо"
        )
        self.show_check.toggled.connect(lambda _on: self._render_recognized())
        rec_layout.addWidget(self.show_check)
        text_splitter.addWidget(rec_box)

        tr_box = QGroupBox("Переклад (Translated)")
        tr_layout = QVBoxLayout(tr_box)
        self.translated_view = QPlainTextEdit()
        self.translated_view.setReadOnly(True)
        self.translated_view.setPlaceholderText("Тут з'явиться переклад…")
        tr_layout.addWidget(self.translated_view)
        text_splitter.addWidget(tr_box)

        text_splitter.setSizes([1, 1])

        # ── Bottom: system log ─────────────────────────────────────────
        log_box = QGroupBox("System Log")
        log_layout = QVBoxLayout(log_box)
        self.status_area = QLabel("Ready")
        self.status_area.setStyleSheet(
            "QLabel { border: 1px solid #ccc; border-radius: 4px; "
            "padding: 4px; background: #f0f0f0; }"
        )
        self.status_area.setMinimumHeight(26)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(120)
        log_layout.addWidget(self.status_area)
        log_layout.addWidget(self.log_view)
        log_box.setMaximumHeight(200)

        # ── Main vertical splitter ─────────────────────────────────────
        vsplit = QSplitter(Qt.Orientation.Vertical)
        vsplit.addWidget(text_splitter)
        vsplit.addWidget(log_box)
        vsplit.setSizes([300, 150])

        # ── Buttons ────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        clear_btn = QPushButton("Clear All")
        clear_btn.clicked.connect(self.clear_all)
        copy_btn = QPushButton("Copy Log")
        copy_btn.clicked.connect(self.copy_log)
        btn_row.addWidget(clear_btn)
        btn_row.addWidget(copy_btn)
        btn_row.addStretch()

        layout.addWidget(vsplit)
        layout.addLayout(btn_row)

    # ── Public API ─────────────────────────────────────────────────────

    def set_status(self, message: str):
        self.status_area.setText(message)

    def add_recognized(self, text: str, check: Optional[str] = None):
        """Append a recognized phrase (whisper) and, when shown, Vosk's text
        of the same audio (``check``; None = no Vosk check for it)."""
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self._recognized.append((ts, text, check))
        if len(self._recognized) > self._max_messages:
            self._recognized = self._recognized[-self._max_messages :]
            self._render_recognized()
            return
        self.recognized_view.appendPlainText(self._recognized_lines(ts, text, check))
        self._scroll_recognized()

    def _recognized_lines(self, ts: str, text: str, check: Optional[str]) -> str:
        line = f"[{ts}] {text}"
        if not self.show_check.isChecked():
            return line
        if check is None:
            return f"{line}\n{CHECK_PREFIX}{CHECK_NONE}"
        return f"{line}\n{CHECK_PREFIX}{check or CHECK_EMPTY}"

    def _render_recognized(self) -> None:
        """Rebuild the panel (the Vosk toggle changed)."""
        self.recognized_view.setPlainText(
            "\n".join(self._recognized_lines(*r) for r in self._recognized)
        )
        self._scroll_recognized()

    def _scroll_recognized(self) -> None:
        self.recognized_view.verticalScrollBar().setValue(
            self.recognized_view.verticalScrollBar().maximum()
        )

    def add_translated(self, text: str):
        """Append a translated segment to the top-right panel."""
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.translated_view.appendPlainText(f"[{ts}] {text}")
        self.translated_view.verticalScrollBar().setValue(
            self.translated_view.verticalScrollBar().maximum()
        )

    def add_log_message(self, message: str, level: str = "INFO"):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{ts}] {level}: {message}")
        lines = self.log_view.toPlainText().split("\n")
        if len(lines) > 500:
            self.log_view.setPlainText("\n".join(lines[-500:]))
        self.log_view.verticalScrollBar().setValue(
            self.log_view.verticalScrollBar().maximum()
        )
        self._messages.append((ts, level, message))
        if len(self._messages) > self._max_messages:
            self._messages = self._messages[-self._max_messages :]

    def add_info(self, message: str):
        self.add_log_message(message, "INFO")

    def add_warning(self, message: str):
        self.add_log_message(message, "WARN")

    def add_error(self, message: str):
        self.add_log_message(message, "ERROR")

    def add_debug(self, message: str):
        self.add_log_message(message, "DEBUG")

    def set_tail(self, text: str) -> None:
        """The grey not-yet-translated tail under the recognized text."""
        if self.tail_label.text() != text:
            self.tail_label.setText(text)

    def clear_all(self):
        self._recognized.clear()
        self.tail_label.setText("")
        self.recognized_view.clear()
        self.translated_view.clear()
        self.log_view.clear()
        self._messages.clear()

    def copy_log(self):
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(self.log_view.toPlainText())

    def get_recent_messages(self, count: int = 10) -> List[Tuple]:
        return self._messages[-count:]


class StatusManager:
    """Bridge between backend events and the StatusLogger widget."""

    def __init__(self, status_logger: StatusLogger = None):
        self.status_logger = status_logger
        self._last_status = ""

    def set_status(self, message: str):
        self._last_status = message
        if self.status_logger:
            self.status_logger.set_status(message)

    def log_recognized(self, text: str, check: Optional[str] = None):
        if self.status_logger:
            self.status_logger.add_recognized(text, check)

    def set_tail(self, text: str):
        if self.status_logger:
            self.status_logger.set_tail(text)

    def log_translated(self, text: str):
        if self.status_logger:
            self.status_logger.add_translated(text)

    def log_info(self, message: str):
        if self.status_logger:
            self.status_logger.add_info(message)
        logger.info(message)

    def log_warning(self, message: str):
        if self.status_logger:
            self.status_logger.add_warning(message)
        logger.warning(message)

    def log_error(self, message: str):
        if self.status_logger:
            self.status_logger.add_error(message)
        logger.error(message)

    def log_debug(self, message: str):
        if self.status_logger:
            self.status_logger.add_debug(message)
        logger.debug(message)
