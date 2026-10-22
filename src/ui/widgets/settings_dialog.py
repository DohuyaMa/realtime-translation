"""Endpoints and segmentation settings for the edge pipeline."""

import os
from typing import Any, Dict, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QLabel,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QDoubleSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.runtime import (
    MODES,
    SOURCE_LANGUAGES,
    EdgeSettings,
    reset_runtime_config,
    resolve_edge_settings,
)


def _lines(widget: QPlainTextEdit) -> list:
    return [ln.strip() for ln in widget.toPlainText().splitlines() if ln.strip()]


def _combo(values, current) -> QComboBox:
    box = QComboBox()
    box.addItems(list(values))
    box.setCurrentText(str(current))
    return box


class EndpointsTab(QWidget):
    """ASR/TTS URLs, LLM source, tokens, mode and source language."""

    def __init__(self, settings: EdgeSettings, parent=None):
        super().__init__(parent)
        form = QFormLayout(self)
        llm = settings.llm
        self.asr_urls = QPlainTextEdit("\n".join(settings.asr_urls))
        self.tts_urls = QPlainTextEdit("\n".join(settings.tts_urls))
        self.django_urls = QPlainTextEdit("\n".join(llm.django_urls))
        self.django_token_file = QLineEdit(llm.django_token_file or "")
        self.llm_service = QLineEdit(llm.service or "")
        self.llm_service.setPlaceholderText("порожньо = авто")
        self.direct_url = QLineEdit(llm.direct_url or "")
        self.direct_token_file = QLineEdit(llm.direct_token_file or "")
        self.model = QLineEdit(llm.model)
        self.api_token_file = QLineEdit(settings.api_token_file or "")
        self.mode = _combo(MODES, settings.mode)
        self.source_language = _combo(SOURCE_LANGUAGES, settings.source_language)
        for widget in (self.asr_urls, self.tts_urls, self.django_urls):
            widget.setFixedHeight(64)
        form.addRow("ASR URL (по одному в рядку):", self.asr_urls)
        form.addRow("TTS URL (по одному в рядку):", self.tts_urls)
        form.addRow("LLM django_urls (по одному в рядку):", self.django_urls)
        form.addRow("LLM django_token_file:", self.django_token_file)
        form.addRow("LLM воркер (service):", self.llm_service)
        form.addRow("LLM direct_url:", self.direct_url)
        form.addRow("LLM direct_token_file:", self.direct_token_file)
        form.addRow("LLM model:", self.model)
        form.addRow("rt api_token_file:", self.api_token_file)
        form.addRow("Режим:", self.mode)
        form.addRow("Мова:", self.source_language)
        self._managed = {
            "asr_urls": self.asr_urls,
            "tts_urls": self.tts_urls,
            "llm.django_urls": self.django_urls,
            "llm.django_token_file": self.django_token_file,
            "llm.service": self.llm_service,
            "llm.direct_url": self.direct_url,
            "llm.direct_token_file": self.direct_token_file,
            "llm.model": self.model,
            "api_token_file": self.api_token_file,
            "mode": self.mode,
        }
        for name in settings.managed:
            widget = self._managed.get(name)
            if widget is not None:
                widget.setEnabled(False)
                widget.setToolTip("Задано через RT_EDGE_* (лише читання)")

    def values(self) -> Dict[str, Any]:
        return {
            "services.asr_urls": _lines(self.asr_urls),
            "services.tts_urls": _lines(self.tts_urls),
            "services.llm.django_urls": _lines(self.django_urls),
            "services.llm.django_token_file": self.django_token_file.text().strip(),
            "services.llm.service": self.llm_service.text().strip(),
            "services.llm.direct_url": self.direct_url.text().strip(),
            "services.llm.direct_token_file": self.direct_token_file.text().strip(),
            "services.llm.model": self.model.text().strip(),
            "services.api_token_file": self.api_token_file.text().strip(),
            "services.mode": self.mode.currentText(),
            "services.source_language": self.source_language.currentText(),
        }


class SegmentationTab(QWidget):
    """transcribe: chunk_min_s/chunk_max_s; translate: soft/hard/end silence."""

    def __init__(self, settings: EdgeSettings, parent=None):
        super().__init__(parent)
        form = QFormLayout(self)
        tr, tl = settings.segmentation.transcribe, settings.segmentation.translate
        self.chunk_min_s = QDoubleSpinBox()
        self.chunk_min_s.setRange(1, 900)
        self.chunk_min_s.setValue(tr.soft_max_ms / 1000)
        self.chunk_max_s = QDoubleSpinBox()
        self.chunk_max_s.setRange(1, 900)
        self.chunk_max_s.setValue(tr.hard_max_ms / 1000)
        self.soft_max_ms = self._ms(tl.soft_max_ms)
        self.hard_max_ms = self._ms(tl.hard_max_ms)
        self.end_silence_ms = self._ms(tl.end_silence_ms or 0)
        self.end_silence_off = QCheckBox("вимкнено (None)")
        self.end_silence_off.setChecked(tl.end_silence_ms is None)
        self.end_silence_ms.setEnabled(tl.end_silence_ms is not None)
        self.end_silence_off.toggled.connect(
            lambda off: self.end_silence_ms.setEnabled(not off)
        )
        form.addRow("transcribe: chunk_min_s", self.chunk_min_s)
        form.addRow("transcribe: chunk_max_s", self.chunk_max_s)
        form.addRow("translate: soft_max_ms", self.soft_max_ms)
        form.addRow("translate: hard_max_ms", self.hard_max_ms)
        form.addRow("translate: end_silence_ms", self.end_silence_ms)
        form.addRow("", self.end_silence_off)

    @staticmethod
    def _ms(value: int) -> QSpinBox:
        box = QSpinBox()
        box.setRange(0, 900_000)
        box.setSingleStep(100)
        box.setValue(int(value))
        return box

    def values(self) -> Dict[str, Any]:
        return {
            "segmentation.transcribe.chunk_min_s": self.chunk_min_s.value(),
            "segmentation.transcribe.chunk_max_s": self.chunk_max_s.value(),
            "segmentation.translate.soft_max_ms": self.soft_max_ms.value(),
            "segmentation.translate.hard_max_ms": self.hard_max_ms.value(),
            "segmentation.translate.end_silence_ms": (
                None
                if self.end_silence_off.isChecked()
                else self.end_silence_ms.value()
            ),
        }


def current_settings() -> EdgeSettings:
    from ...core.config import get_config_manager

    return resolve_edge_settings(get_config_manager().get_all(), os.environ)


def _config_key(managed_name: str) -> str:
    if managed_name.startswith("llm."):
        return "services." + managed_name
    return "services." + managed_name


def build_settings(values: Dict[str, Any]):
    """Resolve settings from form values without saving.

    Returns (settings, values_to_save); keys managed by RT_EDGE_* are
    dropped from values_to_save.
    """
    import copy

    from ...core.config import get_config_manager

    base = get_config_manager().get_all()
    managed = {_config_key(m) for m in current_settings().managed}
    to_save = {k: v for k, v in values.items() if k not in managed}
    merged = copy.deepcopy(base)
    for key, value in to_save.items():
        node = merged
        parts = key.split(".")
        for part in parts[:-1]:
            if not isinstance(node.get(part), dict):
                node[part] = {}
            node = node[part]
        node[parts[-1]] = value
    return resolve_edge_settings(merged, os.environ), to_save


def save_values(to_save: Dict[str, Any]) -> EdgeSettings:
    """Persist dotted config values and return the re-resolved settings."""
    from ...core.config import get_config_manager

    cfg = get_config_manager()
    for key, value in to_save.items():
        cfg.set(key, value)
    cfg.save()
    reset_runtime_config()
    return current_settings()


class EndpointsDialog(QDialog):
    """Endpoints + segmentation. Emits the applied ``EdgeSettings``."""

    settings_changed = Signal(object)

    def __init__(self, settings: Optional[EdgeSettings] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ендпоінти та сегментація")
        self.setMinimumWidth(560)
        settings = settings or current_settings()
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        self.endpoints_tab = EndpointsTab(settings)
        self.segmentation_tab = SegmentationTab(settings)
        tabs.addTab(self.endpoints_tab, "Ендпоінти")
        tabs.addTab(self.segmentation_tab, "Сегментація")
        layout.addWidget(tabs)
        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: red;")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> Dict[str, Any]:
        return {**self.endpoints_tab.values(), **self.segmentation_tab.values()}

    def accept(self):
        try:
            settings, to_save = build_settings(self.values())
            errors = settings.validate()
            if not errors:
                settings = save_values(to_save)
        except Exception as e:  # malformed value: keep dialog open
            errors = [str(e)]
        if errors:
            self.error_label.setText("; ".join(errors))
            return
        self.settings_changed.emit(settings)
        super().accept()


class SettingsButton(QPushButton):
    settings_applied = Signal(object)

    def __init__(self, parent=None):
        super().__init__("Settings", parent)
        self.clicked.connect(self.open_settings)

    def open_settings(self):
        dialog = EndpointsDialog(parent=self.window())
        dialog.settings_changed.connect(self.settings_applied)
        dialog.exec()
