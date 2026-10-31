"""Settings dialog of the edge: services (allow-list, which instance to use),
per-service tabs (ASR / Vosk / LLM / TTS, see service_tabs.py) and
segmentation."""

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

from dataclasses import replace

from ...core.runtime import (
    KINDS,
    MODES,
    OFF,
    OPTIONAL_KINDS,
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
    """Services of the last discovery (cache), for the allow-list / choices."""
    from ...clients.discovery import default_cache_path, parse
    import json

    try:
        return parse(json.loads(default_cache_path().read_text()))[1]
    except (OSError, ValueError):
        return []


def service_choices(kind: str, settings: EdgeSettings, services=None) -> list:
    """Endpoints the user can pick for ``kind`` (this host via loopback)."""
    from ...clients.discovery import Directory, choices

    services = _cached_services() if services is None else services
    return choices(kind, settings, Directory(list(services), "cache"))


AUTO_LABEL = "auto (локальний першим, failover)"
OFF_LABEL = "вимкнено (переклад лише з whisper, без звірки)"


def endpoint_label(e) -> str:
    return f"{e.node} — {e.url}" + (" (локальний)" if e.local else "")


def use_combo(kind: str, settings: EdgeSettings, endpoints) -> QComboBox:
    """auto + every instance of ``kind``; item data = node (None = auto)."""
    box = QComboBox()
    box.addItem(AUTO_LABEL, None)
    if kind in OPTIONAL_KINDS:
        box.addItem(OFF_LABEL, OFF)
    for e in endpoints:
        box.addItem(endpoint_label(e), e.node)
    pinned = getattr(settings.use, kind)
    if pinned is not None:
        idx = box.findData(pinned)
        if idx < 0:
            box.addItem(f"{pinned} (недоступний)", pinned)
            idx = box.count() - 1
        box.setCurrentIndex(idx)
    return box


class EndpointsTab(QWidget):
    """Django discovery, local endpoints, allowed and used services, mode."""

    def __init__(self, settings: EdgeSettings, parent=None, services=None):
        super().__init__(parent)
        services = _cached_services() if services is None else services
        form = QFormLayout(self)
        self.django_urls = QPlainTextEdit("\n".join(settings.django_urls))
        self.django_urls.setFixedHeight(64)
        self.django_token_file = QLineEdit(settings.django_token_file or "")
        self.local_token_file = QLineEdit(settings.local_token_file or "")
        self.local = {
            kind: QLineEdit(getattr(settings.local, kind) or "")
            for kind in ("llm", "asr", "vosk", "tts")
        }
        self.local_node = QLineEdit(settings.local.node or "")
        self.local_node.setPlaceholderText("hostname")
        self.mode = _combo(MODES, settings.mode)
        self.source_language = _combo(SOURCE_LANGUAGES, settings.source_language)
        self.allow_all = QCheckBox("усі знайдені сервіси (і нові)")
        self.allow_all.setChecked(settings.allowed_services is None)
        self.allowed = QListWidget()
        self.allowed.setFixedHeight(130)
        chosen = set(settings.allowed_services or ())
        urls = {}
        for kind in KINDS:
            for e in service_choices(
                kind, replace(settings, allowed_services=None), services
            ):
                urls[f"{kind}:{e.node}"] = endpoint_label(e)
        keys = list(urls) + [e.key for e in services] + sorted(chosen)
        for key in dict.fromkeys(keys):
            item = QListWidgetItem(f"{key}   {urls.get(key, '')}".rstrip())
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            on = settings.allowed_services is None or key in chosen
            item.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
            self.allowed.addItem(item)
        self.allow_all.toggled.connect(self._on_allow_all)
        self.allowed.itemChanged.connect(self._on_item_changed)
        # llm: chosen in the Переклад tab (with the worker's model list)
        self.use = {
            kind: use_combo(kind, settings, service_choices(kind, settings, services))
            for kind in ("asr", "vosk", "tts")
        }
        form.addRow("Django URL (свій хост першим):", self.django_urls)
        form.addRow("DRF token file:", self.django_token_file)
        form.addRow("Цей вузол у discovery:", self.local_node)
        form.addRow("Локальний LLM-воркер:", self.local["llm"])
        form.addRow("Локальний rt-asr:", self.local["asr"])
        form.addRow("Локальний rt-vosk:", self.local["vosk"])
        form.addRow("Локальний rt-tts:", self.local["tts"])
        form.addRow("Локальний токен воркера:", self.local_token_file)
        form.addRow("Дозволені сервіси:", self.allow_all)
        form.addRow("", self.allowed)
        form.addRow("Використовувати ASR:", self.use["asr"])
        form.addRow("Звірка Vosk:", self.use["vosk"])
        form.addRow("Використовувати TTS:", self.use["tts"])
        form.addRow("Режим:", self.mode)
        form.addRow("Мова:", self.source_language)
        self._managed = {
            "django_urls": self.django_urls,
            "django_token_file": self.django_token_file,
            "local_token_file": self.local_token_file,
            "local.llm": self.local["llm"],
            "local.asr": self.local["asr"],
            "local.vosk": self.local["vosk"],
            "local.tts": self.local["tts"],
            "local.node": self.local_node,
            "allowed_services": self.allowed,
            "mode": self.mode,
        }
        for name in settings.managed:
            widget = self._managed.get(name)
            if widget is not None:
                widget.setEnabled(False)
                widget.setToolTip("Задано через RT_EDGE_* (лише читання)")
        if "allowed_services" in settings.managed:
            self.allow_all.setEnabled(False)

    def _items(self):
        return [self.allowed.item(i) for i in range(self.allowed.count())]

    def _on_allow_all(self, on: bool) -> None:
        if on:
            self.allowed.blockSignals(True)
            for item in self._items():
                item.setCheckState(Qt.CheckState.Checked)
            self.allowed.blockSignals(False)

    def _on_item_changed(self, item) -> None:
        if item.checkState() != Qt.CheckState.Checked:
            self.allow_all.setChecked(False)

    def allowed_values(self):
        if self.allow_all.isChecked():
            return None
        return [
            item.data(Qt.ItemDataRole.UserRole)
            for item in self._items()
            if item.checkState() == Qt.CheckState.Checked
        ]

    def values(self) -> Dict[str, Any]:
        return {
            "services.django_urls": _lines(self.django_urls),
            "services.django_token_file": self.django_token_file.text().strip(),
            "services.local_token_file": self.local_token_file.text().strip(),
            **{f"services.local.{k}": w.text().strip() for k, w in self.local.items()},
            "services.local.node": self.local_node.text().strip() or None,
            "services.allowed_services": self.allowed_values(),
            **{f"services.use.{k}": w.currentData() for k, w in self.use.items()},
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
        form.addRow("transcribe: chunk_min_s", self.chunk_min_s)
        form.addRow("transcribe: chunk_max_s", self.chunk_max_s)
        form.addRow("translate: soft_max_ms", self.soft_max_ms)
        form.addRow("translate: hard_max_ms", self.hard_max_ms)
        form.addRow(
            "",
            QLabel("Пауза, після якої фраза йде в переклад: вкладка «Переклад (LLM)»"),
        )

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
    """Persist dotted config values and return the re-resolved settings.

    Only real overrides are written: a value that resolves to the same
    settings as leaving the key out (the default of config/default.yml / the
    code) is removed from config.yml, so later default changes apply."""
    from ...core.config import get_config_manager

    cfg = get_config_manager()
    for key, value in to_save.items():
        cfg.unset(key)
        default = resolve_edge_settings(cfg.get_all(), {})
        cfg.set(key, value)
        if resolve_edge_settings(cfg.get_all(), {}) == default:
            cfg.unset(key)
    cfg.save()
    reset_runtime_config()
    return current_settings()


def service_client_factory(kind: str, settings: EdgeSettings):
    """Endpoint -> client for the settings dialog (config, models, voices)."""
    from ...clients import client_factories

    if kind == "llm":  # llama worker: GET /v1/models with its token
        from ...clients import ServiceClient

        t = settings.timeouts
        return lambda e: ServiceClient(
            e.url,
            credential=e.credential,
            timeout_s=10.0,
            connect_timeout_s=t.connect_s,
        )
    return getattr(client_factories(settings), kind)


class SettingsDialog(QDialog):
    """Endpoints/choice, per-service settings, segmentation.

    Emits the applied ``EdgeSettings`` on OK; service node settings are
    applied separately with each panel's own button.
    """

    settings_changed = Signal(object)

    def __init__(
        self,
        settings: Optional[EdgeSettings] = None,
        parent=None,
        services=None,
        background=None,
    ):
        super().__init__(parent)
        from .service_tabs import AsrTab, Background, LlmTab, TtsTab, VoskTab

        self.setWindowTitle("Налаштування")
        self.setMinimumWidth(640)
        settings = settings or current_settings()
        services = _cached_services() if services is None else services
        self.bg = background or Background(self)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        self.endpoints_tab = EndpointsTab(settings, services=services)
        self.asr_tab = AsrTab(
            settings,
            service_choices("asr", settings, services),
            service_client_factory("asr", settings),
            self.bg,
        )
        self.vosk_tab = VoskTab(
            service_choices("vosk", settings, services),
            service_client_factory("vosk", settings),
            self.bg,
        )
        self.tts_tab = TtsTab(
            settings,
            service_choices("tts", settings, services),
            service_client_factory("tts", settings),
            self.bg,
        )
        self.llm_tab = LlmTab(
            settings,
            services,
            service_choices("llm", settings, services),
            service_client_factory("llm", settings),
            self.bg,
        )
        self.segmentation_tab = SegmentationTab(settings)
        tabs.addTab(self.endpoints_tab, "Сервіси")
        tabs.addTab(self.asr_tab, "Розпізнавання (ASR)")
        tabs.addTab(self.vosk_tab, "Звірка (Vosk)")
        tabs.addTab(self.llm_tab, "Переклад (LLM)")
        tabs.addTab(self.tts_tab, "Озвучка (TTS)")
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
        return {
            **self.endpoints_tab.values(),
            **self.asr_tab.values(),
            **self.llm_tab.values(),
            **self.tts_tab.values(),
            **self.segmentation_tab.values(),
        }

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


def apply_use(kind: str, node: Optional[str]):
    """Persist ``services.use.<kind>`` (status panel choice); returns
    (settings, errors) - settings is None when rejected."""
    settings, to_save = build_settings({f"services.use.{kind}": node})
    errors = settings.validate()
    if errors:
        return None, errors
    return save_values(to_save), []


class SettingsButton(QPushButton):
    settings_applied = Signal(object)

    def __init__(self, parent=None):
        super().__init__("Settings", parent)
        self.clicked.connect(self.open_settings)

    def open_settings(self):
        dialog = SettingsDialog(parent=self.window())
        dialog.settings_changed.connect(self.settings_applied)
        dialog.exec()
