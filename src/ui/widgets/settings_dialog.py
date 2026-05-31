"""Settings dialog with General and Models tabs."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFormLayout, QGroupBox, QLineEdit,
    QPushButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from .model_tab import ModelTab


_TRANSLATION_MODELS = [
    "Helsinki-NLP/opus-mt-uk-en",
    "facebook/nllb-200-distilled-600M",
]

_TTS_VOICES = [
    "af_heart",
    "af_bella",
    "af_nicole",
    "af_sky",
    "af_sarah",
    "af_river",
    "am_adam",
    "am_liam",
    "am_eric",
    "am_michael",
    "bf_emma",
    "bf_isabella",
    "bm_daniel",
    "bm_george",
]


def _load_config():
    """Load config values from ConfigManager, return flat dict."""
    from ...core.config import get_config_manager
    cfg = get_config_manager()
    return {
        "use_wyoming": cfg.get("wyoming.use_wyoming", False),
        "wyoming_host": cfg.get("wyoming.host", "localhost"),
        "wyoming_port": cfg.get("wyoming.port", 10300),
        "sample_rate": cfg.get("audio.sample_rate", 16000),
        "source_lang": cfg.get("translation.source_lang", "auto"),
        "target_lang": cfg.get("translation.target_lang", "en"),
        "whisper_model": cfg.get("models.whisper.model", "small"),
        "wyoming_model": cfg.get("models.whisper.wyoming_model", "small-int8"),
        "translate_model": cfg.get("models.translate.model", _TRANSLATION_MODELS[0]),
        "tts_voice": cfg.get("models.tts.voice", _TTS_VOICES[0]),
        "tts_speed": cfg.get("models.tts.speed", 1.0),
        "beam_size": cfg.get("models.whisper.beam_size", 5),
        "temperature": cfg.get("models.whisper.temperature", 0.0),
        "num_beams": cfg.get("models.translate.num_beams", 4),
        "repetition_penalty": cfg.get("models.translate.repetition_penalty", 1.2),
        "max_length": cfg.get("models.translate.max_length", 200),
    }


def _save_config(settings: dict):
    """Save all settings to ConfigManager."""
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
    cfg.set("models.whisper.wyoming_model", settings.get("wyoming_model", "small-int8"))
    cfg.set("models.translate.model", settings.get("translate_model", _TRANSLATION_MODELS[0]))
    cfg.set("models.tts.voice", settings.get("tts_voice", _TTS_VOICES[0]))
    cfg.set("models.tts.speed", settings.get("tts_speed", 1.0))
    cfg.set("models.whisper.beam_size", settings.get("beam_size", 5))
    cfg.set("models.whisper.temperature", settings.get("temperature", 0.0))
    cfg.set("models.translate.num_beams", settings.get("num_beams", 4))
    cfg.set("models.translate.repetition_penalty", settings.get("repetition_penalty", 1.2))
    cfg.set("models.translate.max_length", settings.get("max_length", 200))
    cfg.save()


class _GeneralTab(QWidget):
    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self._cfg = config
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)

        wyoming_group = QGroupBox("Wyoming Service Configuration")
        wyoming_layout = QFormLayout()

        self.wyoming_enabled = QCheckBox("Use Wyoming ASR Service")
        self.wyoming_enabled.setChecked(self._cfg.get("use_wyoming", False))
        wyoming_layout.addRow("Enable Wyoming:", self.wyoming_enabled)

        self.wyoming_host = QLineEdit()
        self.wyoming_host.setText(self._cfg.get("wyoming_host", "localhost"))
        wyoming_layout.addRow("Wyoming Host:", self.wyoming_host)

        self.wyoming_port = QSpinBox()
        self.wyoming_port.setRange(1, 65535)
        self.wyoming_port.setValue(self._cfg.get("wyoming_port", 10300))
        wyoming_layout.addRow("Wyoming Port:", self.wyoming_port)

        wyoming_group.setLayout(wyoming_layout)
        layout.addWidget(wyoming_group)

        audio_group = QGroupBox("Audio Settings")
        audio_layout = QFormLayout()

        self.sample_rate = QSpinBox()
        self.sample_rate.setRange(8000, 48000)
        self.sample_rate.setValue(self._cfg.get("sample_rate", 16000))
        self.sample_rate.setSuffix(" Hz")
        audio_layout.addRow("Sample Rate:", self.sample_rate)

        audio_group.setLayout(audio_layout)
        layout.addWidget(audio_group)

        translation_group = QGroupBox("Translation Settings")
        translation_layout = QFormLayout()

        self.source_lang = QComboBox()
        self.source_lang.addItems([
            "Auto", "Ukrainian (uk)", "Polish (pl)", "English (en)",
            "German (de)", "French (fr)", "Spanish (es)",
        ])
        self.source_lang.setCurrentIndex(
            self._lang_index(self._cfg.get("source_lang", "auto"))
        )
        translation_layout.addRow("Source Language:", self.source_lang)

        self.target_lang = QComboBox()
        self.target_lang.addItems([
            "English (en)", "Ukrainian (uk)", "Polish (pl)",
            "German (de)", "French (fr)", "Spanish (es)",
        ])
        self.target_lang.setCurrentIndex(
            self._target_lang_index(self._cfg.get("target_lang", "en"))
        )
        translation_layout.addRow("Target Language:", self.target_lang)

        self.translate_model = QComboBox()
        self.translate_model.addItems(_TRANSLATION_MODELS)
        current_tm = self._cfg.get("translate_model", _TRANSLATION_MODELS[0])
        tm_idx = _TRANSLATION_MODELS.index(current_tm) if current_tm in _TRANSLATION_MODELS else 0
        self.translate_model.setCurrentIndex(tm_idx)
        translation_layout.addRow("Translation Model:", self.translate_model)

        translation_group.setLayout(translation_layout)
        layout.addWidget(translation_group)

        tts_group = QGroupBox("TTS Settings")
        tts_layout = QFormLayout()

        self.tts_voice = QComboBox()
        self.tts_voice.addItems(_TTS_VOICES)
        current_voice = self._cfg.get("tts_voice", _TTS_VOICES[0])
        v_idx = _TTS_VOICES.index(current_voice) if current_voice in _TTS_VOICES else 0
        self.tts_voice.setCurrentIndex(v_idx)
        tts_layout.addRow("TTS Voice:", self.tts_voice)

        self.tts_speed = QDoubleSpinBox()
        self.tts_speed.setRange(0.5, 2.0)
        self.tts_speed.setSingleStep(0.1)
        self.tts_speed.setDecimals(1)
        self.tts_speed.setValue(self._cfg.get("tts_speed", 1.0))
        self.tts_speed.setSuffix("x")
        tts_layout.addRow("Speed:", self.tts_speed)

        tts_group.setLayout(tts_layout)
        layout.addWidget(tts_group)

        recog_group = QGroupBox("Recognition Tuning")
        recog_layout = QFormLayout()

        self.beam_size = QSpinBox()
        self.beam_size.setRange(1, 10)
        self.beam_size.setValue(self._cfg.get("beam_size", 5))
        self.beam_size.setSuffix(" beams")
        recog_layout.addRow("Beam Size:", self.beam_size)

        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(0.0, 1.0)
        self.temperature.setSingleStep(0.1)
        self.temperature.setDecimals(1)
        self.temperature.setValue(self._cfg.get("temperature", 0.0))
        self.temperature.setSuffix(" temp")
        recog_layout.addRow("Temperature:", self.temperature)

        recog_group.setLayout(recog_layout)
        layout.addWidget(recog_group)

        trans_tune_group = QGroupBox("Translation Tuning")
        trans_tune_layout = QFormLayout()

        self.num_beams = QSpinBox()
        self.num_beams.setRange(1, 10)
        self.num_beams.setValue(self._cfg.get("num_beams", 4))
        self.num_beams.setSuffix(" beams")
        trans_tune_layout.addRow("Num Beams:", self.num_beams)

        self.repetition_penalty = QDoubleSpinBox()
        self.repetition_penalty.setRange(1.0, 2.0)
        self.repetition_penalty.setSingleStep(0.1)
        self.repetition_penalty.setDecimals(1)
        self.repetition_penalty.setValue(self._cfg.get("repetition_penalty", 1.2))
        self.repetition_penalty.setSuffix(" rep")
        trans_tune_layout.addRow("Repetition Penalty:", self.repetition_penalty)

        self.max_length = QSpinBox()
        self.max_length.setRange(50, 500)
        self.max_length.setSingleStep(10)
        self.max_length.setValue(self._cfg.get("max_length", 200))
        self.max_length.setSuffix(" tokens")
        trans_tune_layout.addRow("Max Length:", self.max_length)

        trans_tune_group.setLayout(trans_tune_layout)
        layout.addWidget(trans_tune_group)

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
            "translate_model": _TRANSLATION_MODELS[self.translate_model.currentIndex()],
            "tts_voice": _TTS_VOICES[self.tts_voice.currentIndex()],
            "tts_speed": self.tts_speed.value(),
            "beam_size": self.beam_size.value(),
            "temperature": self.temperature.value(),
            "num_beams": self.num_beams.value(),
            "repetition_penalty": self.repetition_penalty.value(),
            "max_length": self.max_length.value(),
        }


class SettingsDialog(QDialog):
    settings_changed = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.setMinimumWidth(560)
        self.setMinimumHeight(720)
        self._build()

    def _build(self):
        config = _load_config()

        layout = QVBoxLayout(self)

        tabs = QTabWidget()

        self._general = _GeneralTab(config)
        tabs.addTab(self._general, "General")

        self._models = ModelTab(config)
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

        _save_config(settings)

        self.settings_changed.emit(settings)
        super().accept()


class SettingsButton(QPushButton):
    settings_applied = Signal(dict)

    def __init__(self, parent=None):
        super().__init__("Settings", parent)
        self.clicked.connect(self.open_settings)

    def open_settings(self):
        dialog = SettingsDialog(self.parent())
        dialog.settings_changed.connect(self.settings_applied)
        dialog.exec()
