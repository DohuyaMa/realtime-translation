"""Endpoints and segmentation settings for the edge pipeline."""

import os
from typing import Any, Dict, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QLabel,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
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


def _cached_services() -> list:
    """Services of the last discovery (cache), for the allow-list."""
    from ...clients.discovery import default_cache_path, parse
    import json

    try:
        return parse(json.loads(default_cache_path().read_text()))[1]
    except (OSError, ValueError):
        return []


class EndpointsTab(QWidget):
    """Django discovery, local endpoints, allowed services, mode, language."""

    def __init__(self, settings: EdgeSettings, parent=None, services=None):
        super().__init__(parent)
        form = QFormLayout(self)
        self.django_urls = QPlainTextEdit("\n".join(settings.django_urls))
        self.django_urls.setFixedHeight(64)
        self.django_token_file = QLineEdit(settings.django_token_file or "")
        self.local_token_file = QLineEdit(settings.local_token_file or "")
        self.local = {
            kind: QLineEdit(getattr(settings.local, kind) or "")
            for kind in ("llm", "asr", "tts")
        }
        self.model = QLineEdit(settings.llm.model)
        self.mode = _combo(MODES, settings.mode)
        self.source_language = _combo(SOURCE_LANGUAGES, settings.source_language)
        self.allow_all = QCheckBox("усі знайдені сервіси")
        self.allow_all.setChecked(settings.allowed_services is None)
        self.allowed = QListWidget()
        self.allowed.setFixedHeight(110)
        chosen = set(settings.allowed_services or ())
        keys = [e.key for e in (_cached_services() if services is None else services)]
        for key in dict.fromkeys(list(keys) + sorted(chosen)):
            item = QListWidgetItem(key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            on = settings.allowed_services is None or key in chosen
            item.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
            self.allowed.addItem(item)
        self.allowed.setEnabled(not self.allow_all.isChecked())
        self.allow_all.toggled.connect(lambda on: self.allowed.setEnabled(not on))
        form.addRow("Django URL (свій хост першим):", self.django_urls)
        form.addRow("DRF token file:", self.django_token_file)
        form.addRow("Локальний LLM-воркер:", self.local["llm"])
        form.addRow("Локальний rt-asr:", self.local["asr"])
        form.addRow("Локальний rt-tts:", self.local["tts"])
        form.addRow("Локальний токен воркера:", self.local_token_file)
        form.addRow("Дозволені сервіси:", self.allow_all)
        form.addRow("", self.allowed)
        form.addRow("LLM model:", self.model)
        form.addRow("Режим:", self.mode)
        form.addRow("Мова:", self.source_language)
        self._managed = {
            "django_urls": self.django_urls,
            "django_token_file": self.django_token_file,
            "local_token_file": self.local_token_file,
            "local.llm": self.local["llm"],
            "local.asr": self.local["asr"],
            "local.tts": self.local["tts"],
            "allowed_services": self.allowed,
            "llm.model": self.model,
            "mode": self.mode,
        }
        for name in settings.managed:
            widget = self._managed.get(name)
            if widget is not None:
                widget.setEnabled(False)
                widget.setToolTip("Задано через RT_EDGE_* (лише читання)")
        if "allowed_services" in settings.managed:
            self.allow_all.setEnabled(False)

    def allowed_values(self):
        if self.allow_all.isChecked():
            return None
        return [
            self.allowed.item(i).text()
            for i in range(self.allowed.count())
            if self.allowed.item(i).checkState() == Qt.CheckState.Checked
        ]

    def values(self) -> Dict[str, Any]:
        return {
            "services.django_urls": _lines(self.django_urls),
            "services.django_token_file": self.django_token_file.text().strip(),
            "services.local_token_file": self.local_token_file.text().strip(),
            **{f"services.local.{k}": w.text().strip() for k, w in self.local.items()},
            "services.allowed_services": self.allowed_values(),
            "services.llm.model": self.model.text().strip(),
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
