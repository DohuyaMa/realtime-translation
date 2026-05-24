"""Settings dialog with General and Models tabs."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSpinBox, QTabWidget,
    QVBoxLayout, QWidget,
)
from loguru import logger

from .model_tab import ModelTab


class _GeneralTab(QWidget):
    def __init__(self, current_settings: dict, parent=None):
        super().__init__(parent)
        self._s = current_settings
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)

        # Wyoming
        wyoming_group = QGroupBox("Wyoming Service Configuration")
        wyoming_layout = QFormLayout()

        self.wyoming_enabled = QCheckBox("Use Wyoming ASR Service")
        self.wyoming_enabled.setChecked(self._s.get("use_wyoming", False))
        wyoming_layout.addRow("Enable Wyoming:", self.wyoming_enabled)

        self.wyoming_host = QLineEdit()
        self.wyoming_host.setText(self._s.get("wyoming_host", "localhost"))
        wyoming_layout.addRow("Wyoming Host:", self.wyoming_host)

        self.wyoming_port = QSpinBox()
        self.wyoming_port.setRange(1, 65535)
        self.wyoming_port.setValue(self._s.get("wyoming_port", 10300))
        wyoming_layout.addRow("Wyoming Port:", self.wyoming_port)

        wyoming_group.setLayout(wyoming_layout)
        layout.addWidget(wyoming_group)

        # Audio
        audio_group = QGroupBox("Audio Settings")
        audio_layout = QFormLayout()

        self.sample_rate = QSpinBox()
        self.sample_rate.setRange(8000, 48000)
        self.sample_rate.setValue(self._s.get("sample_rate", 16000))
        self.sample_rate.setSuffix(" Hz")
        audio_layout.addRow("Sample Rate:", self.sample_rate)

        audio_group.setLayout(audio_layout)
        layout.addWidget(audio_group)

        # Translation
        translation_group = QGroupBox("Translation Settings")
        translation_layout = QFormLayout()

        self.source_lang = QComboBox()
        self.source_lang.addItems([
            "Auto", "Ukrainian (uk)", "Polish (pl)", "English (en)",
            "German (de)", "French (fr)", "Spanish (es)",
        ])
        self.source_lang.setCurrentIndex(
            self._lang_index(self._s.get("source_lang", "auto"))
        )
        translation_layout.addRow("Source Language:", self.source_lang)

        self.target_lang = QComboBox()
        self.target_lang.addItems([
            "English (en)", "Ukrainian (uk)", "Polish (pl)",
            "German (de)", "French (fr)", "Spanish (es)",
        ])
        self.target_lang.setCurrentIndex(
            self._target_lang_index(self._s.get("target_lang", "en"))
        )
        translation_layout.addRow("Target Language:", self.target_lang)

        translation_group.setLayout(translation_layout)
        layout.addWidget(translation_group)
        layout.addStretch()

        self.wyoming_enabled.stateChanged.connect(self._on_wyoming_toggle)
        self._on_wyoming_toggle(self.wyoming_enabled.isChecked())

    def _on_wyoming_toggle(self, checked):
        self.wyoming_host.setEnabled(bool(checked))
        self.wyoming_port.setEnabled(bool(checked))

    def _lang_index(self, code):
        return {"auto": 0, "uk": 1, "pl": 2, "en": 3, "de": 4, "fr": 5, "es": 6}.get(code, 0)

    def _target_lang_index(self, code):
        return {"en": 0, "uk": 1, "pl": 2, "de": 3, "fr": 4, "es": 5}.get(code, 0)

    def get_settings(self) -> dict:
        src_codes = ["auto", "uk", "pl", "en", "de", "fr", "es"]
        tgt_codes = ["en", "uk", "pl", "de", "fr", "es"]
        return {
            "use_wyoming": self.wyoming_enabled.isChecked(),
            "wyoming_host": self.wyoming_host.text(),
            "wyoming_port": self.wyoming_port.value(),
            "sample_rate": self.sample_rate.value(),
            "source_lang": src_codes[self.source_lang.currentIndex()],
            "target_lang": tgt_codes[self.target_lang.currentIndex()],
        }


class SettingsDialog(QDialog):
    settings_changed = Signal(dict)

    def __init__(self, current_settings=None, parent=None):
        super().__init__(parent)
        self.current_settings = current_settings or {}
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.setMinimumWidth(560)
        self.setMinimumHeight(520)
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)

        tabs = QTabWidget()

        self._general = _GeneralTab(self.current_settings)
        tabs.addTab(self._general, "General")

        self._models = ModelTab(self.current_settings)
        tabs.addTab(self._models, "Models")

        layout.addWidget(tabs)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        general = self._general.get_settings()
        model = self._models.get_settings()

        settings = {**general, **model}

        from ...core.config import get_config_manager
        cfg = get_config_manager()
        cfg.set_wyoming_config(
            use_wyoming=settings["use_wyoming"],
            host=settings["wyoming_host"],
            port=settings["wyoming_port"],
        )
        cfg.set("audio.sample_rate", settings["sample_rate"])
        cfg.set("translation.source_lang", settings["source_lang"])
        cfg.set("translation.target_lang", settings["target_lang"])
        cfg.set("models.whisper.backend", "wyoming" if settings["use_wyoming"] else "local")
        cfg.set("models.whisper.model", settings["whisper_model"])
        cfg.save()

        self.settings_changed.emit(settings)
        super().accept()


class SettingsButton(QPushButton):
    settings_applied = Signal(dict)

    def __init__(self, parent=None):
        super().__init__("Settings", parent)
        self.clicked.connect(self.open_settings)

    def open_settings(self):
        current_settings = getattr(self.parent(), "current_settings", {})
        dialog = SettingsDialog(current_settings, self.parent())
        dialog.settings_changed.connect(self.settings_applied)
        dialog.exec()
