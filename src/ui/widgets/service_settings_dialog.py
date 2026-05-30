"""Service-specific settings dialog for the real-time translation system."""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QGroupBox, QCheckBox, QPushButton, QLineEdit,
    QLabel, QComboBox, QSpinBox, QDialogButtonBox,
    QMessageBox, QProgressBar
)
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QFont
from loguru import logger
import re
import sys


def _get_running_whisper_info() -> dict:
    """Read whisper model/device info — NO subprocess (no fork in Qt main thread).

    Reads from:
    1. ~/.config/systemd/user/rt-whisper.service  (deployed unit file, no subprocess)
    2. ~/.config/real-time-translator/config.yml  (UI-set model override)
    3. /proc/<pid>/status + /proc/<pid>/cmdline  (running process, no fork)
    """
    from pathlib import Path
    import yaml

    result = {'model': '?', 'device': '?', 'compute_type': '?', 'active': 'unknown'}

    # --- 1. Read model from config file (UI override has priority) ---
    try:
        cfg_path = Path.home() / ".config" / "real-time-translator" / "config.yml"
        cfg = yaml.safe_load(cfg_path.read_text()) or {}
        result['model'] = cfg.get('models', {}).get('whisper', {}).get('model', '?')
    except Exception:
        pass

    # --- 2. Read device/compute from deployed systemd unit file ---
    try:
        unit_path = Path.home() / ".config" / "systemd" / "user" / "rt-whisper.service"
        unit_text = unit_path.read_text()
        for line in unit_text.splitlines():
            if 'ExecStart' in line:
                if m := re.search(r'--device\s+(\S+)', line):
                    result['device'] = m.group(1)
                if m := re.search(r'--compute-type\s+(\S+)', line):
                    result['compute_type'] = m.group(1)
                # model from Nix (only if not overridden by config)
                if result['model'] == '?' and (m := re.search(r'--model\s+(\S+)', line)):
                    result['model'] = m.group(1)
                break
    except Exception:
        pass

    # --- 3. Check if service is running via /proc (no fork needed) ---
    try:
        for pid_dir in Path('/proc').iterdir():
            if not pid_dir.name.isdigit():
                continue
            try:
                cmdline = (pid_dir / 'cmdline').read_bytes().replace(b'\x00', b' ').decode()
                if 'translator-whisper' in cmdline and '--socket-path' in cmdline:
                    result['active'] = 'active'
                    break
            except Exception:
                continue
    except Exception:
        pass

    return result


class ServiceSettingsDialog(QDialog):
    """Service-specific settings dialog."""

    settings_changed = Signal(str, dict)   # service_name, settings

    # Internal cross-thread signals (background thread → main thread UI update)
    _sig_progress = Signal(int, str)       # pct, message
    _sig_done     = Signal(bool, str)      # success, model_name
    
    def __init__(self, service_name: str, current_settings=None, parent=None):
        super().__init__(parent)
        self.service_name = service_name
        self.current_settings = current_settings or {}
        # Remove "Service" from title and use "Set" prefix format
        services_map = {
            "capture": "Capture",
            "whisper": "Whisper",
            "translate": "Translation",
            "tts": "TTS",
            "playback": "Playback"
        }
        service_display_name = services_map.get(service_name, service_name.capitalize())
        self.setWindowTitle(f"Set {service_display_name}")
        self.setModal(True)
        self.setMinimumWidth(400)

        # Wire cross-thread signals to slots — connections are made on main thread,
        # so Qt will always dispatch the slot call back to the main thread.
        self._sig_progress.connect(self._on_model_progress)
        self._sig_done.connect(self._on_model_done)

        self.init_ui()
        
    def init_ui(self):
        """Initialize the service settings UI."""
        layout = QVBoxLayout(self)
        
        if self.service_name == 'whisper':
            self._create_whisper_settings(layout)
        elif self.service_name == 'translate':
            self._create_translate_settings(layout)
        elif self.service_name == 'tts':
            self._create_tts_settings(layout)
        elif self.service_name == 'capture':
            self._create_capture_settings(layout)
        elif self.service_name == 'playback':
            self._create_playback_settings(layout)
        else:
            # Generic settings for unknown services
            label = QLabel(f"Settings for {self.service_name} service would be configured here.")
            layout.addWidget(label)
        
        # Load current configuration from config manager
        self._load_current_config()
        
        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
    
    def _create_whisper_settings(self, layout):
        """Create settings UI for Whisper service."""

        # ── Running service info (read-only header) ───────────────────────
        info = _get_running_whisper_info()
        status_color = "green" if info['active'] == 'active' else "red"
        info_group = QGroupBox("Currently Running (rt-whisper)")
        info_layout = QFormLayout()

        status_lbl = QLabel(info['active'])
        status_lbl.setStyleSheet(f"color: {status_color};")
        bold = QFont()
        bold.setBold(True)
        status_lbl.setFont(bold)
        info_layout.addRow("Status:", status_lbl)

        self._running_model_lbl = QLabel(info['model'])
        self._running_model_lbl.setFont(bold)
        info_layout.addRow("Active model:", self._running_model_lbl)

        device_lbl = QLabel(f"{info['device']}  ({info['compute_type']})")
        info_layout.addRow("Device / Compute:", device_lbl)

        info_group.setLayout(info_layout)
        layout.addWidget(info_group)

        # ── Model selection ───────────────────────────────────────────────
        model_group = QGroupBox("Change Local Whisper Model")
        model_layout = QVBoxLayout()

        row = QHBoxLayout()
        row.addWidget(QLabel("Model:"))

        self.model_type = QComboBox()
        models = ["tiny", "base", "small", "medium", "large"]
        sizes  = {"tiny": "~150 MB", "base": "~300 MB", "small": "~500 MB",
                  "medium": "~1.5 GB", "large": "~3 GB"}
        for m in models:
            self.model_type.addItem(f"{m}  ({sizes[m]})", userData=m)

        # Default selection: running model from config (not systemd CLI)
        try:
            from ...core.config import get_config_manager
            cfg_model = get_config_manager().get("models.whisper.model", info['model'])
        except Exception:
            cfg_model = info['model']
        idx = next((i for i, m in enumerate(models) if m == cfg_model), 0)
        self.model_type.setCurrentIndex(idx)
        row.addWidget(self.model_type)

        self._apply_model_btn = QPushButton("Apply")
        self._apply_model_btn.setToolTip(
            "Downloads model if not cached, saves to config, restarts whisper service"
        )
        self._apply_model_btn.clicked.connect(self._on_apply_model)
        row.addWidget(self._apply_model_btn)
        model_layout.addLayout(row)

        self._model_progress = QProgressBar()
        self._model_progress.setVisible(False)
        self._model_progress.setRange(0, 100)
        model_layout.addWidget(self._model_progress)

        self._model_status_lbl = QLabel("")
        self._model_status_lbl.setWordWrap(True)
        self._model_status_lbl.setStyleSheet("color: gray; font-size: 9pt;")
        model_layout.addWidget(self._model_status_lbl)

        model_group.setLayout(model_layout)
        layout.addWidget(model_group)

        # ── Wyoming settings ──────────────────────────────────────────────
        wyoming_group = QGroupBox("Wyoming ASR Routing")
        wyoming_layout = QFormLayout()

        self.wyoming_enabled = QCheckBox("Route via Wyoming instead of local whisper")
        self.wyoming_enabled.setChecked(self.current_settings.get('use_wyoming', False))
        wyoming_layout.addRow("Enable Wyoming:", self.wyoming_enabled)

        self.wyoming_host = QLineEdit()
        self.wyoming_host.setText(self.current_settings.get('wyoming_host', 'localhost'))
        wyoming_layout.addRow("Wyoming Host:", self.wyoming_host)

        self.wyoming_port = QSpinBox()
        self.wyoming_port.setRange(1, 65535)
        self.wyoming_port.setValue(self.current_settings.get('wyoming_port', 10300))
        wyoming_layout.addRow("Wyoming Port:", self.wyoming_port)

        wyoming_group.setLayout(wyoming_layout)
        layout.addWidget(wyoming_group)

        self.wyoming_enabled.stateChanged.connect(self._on_wyoming_enabled_changed)
        self._on_wyoming_enabled_changed(self.wyoming_enabled.isChecked())

    def _on_apply_model(self):
        model = self.model_type.currentData()
        self._apply_model_btn.setEnabled(False)
        self._model_progress.setVisible(True)
        self._model_progress.setValue(0)
        self._model_status_lbl.setText("Starting...")

        # Find the ui_controller through the parent chain
        ui_ctrl = self._find_ui_controller()
        if ui_ctrl is None:
            self._model_status_lbl.setText("Error: cannot reach UI controller")
            self._apply_model_btn.setEnabled(True)
            return

        def _progress(pct, msg):
            # Safe from any thread: Signal.emit() is thread-safe in Qt/PySide6
            self._sig_progress.emit(int(pct), str(msg))

        def _done(ok):
            self._sig_done.emit(bool(ok), str(model))

        ui_ctrl.change_whisper_model(model, progress_cb=_progress, done_cb=_done)

    def _find_ui_controller(self):
        """Walk up the widget tree to find a ui_controller attribute."""
        w = self.parent()
        while w is not None:
            ctrl = getattr(w, 'ui_controller', None)
            if ctrl is not None:
                return ctrl
            w = w.parent() if hasattr(w, 'parent') else None
        return None

    @Slot(int, str)
    def _on_model_progress(self, pct: int, msg: str):
        if pct < 0:
            self._model_progress.setValue(0)
            self._model_status_lbl.setStyleSheet("color: red; font-size: 9pt;")
        else:
            self._model_progress.setValue(min(pct, 100))
            self._model_status_lbl.setStyleSheet("color: gray; font-size: 9pt;")
        self._model_status_lbl.setText(msg)

    @Slot(bool, str)
    def _on_model_done(self, ok: bool, model: str):
        self._apply_model_btn.setEnabled(True)
        if ok:
            self._model_progress.setValue(100)
            self._running_model_lbl.setText(model)
            self._model_status_lbl.setStyleSheet("color: green; font-size: 9pt;")
            self._model_status_lbl.setText(f"Done — whisper restarted with model '{model}'")
        else:
            self._model_progress.setValue(0)
            self._model_status_lbl.setStyleSheet("color: red; font-size: 9pt;")
            self._model_status_lbl.setText("Failed — see logs for details")
    
    def _create_translate_settings(self, layout):
        """Create settings UI for Translation service."""
        # Translation settings group
        translate_group = QGroupBox("Translation Configuration")
        translate_layout = QFormLayout()
        
        # Source language
        self.source_lang = QComboBox()
        self.source_lang.addItems(["Auto", "Ukrainian (uk)", "Polish (pl)", "English (en)", "German (de)", "French (fr)", "Spanish (es)"])
        current_source = self.current_settings.get('source_lang', 'auto')
        source_index = self._get_lang_index(current_source)
        self.source_lang.setCurrentIndex(source_index)
        translate_layout.addRow("Source Language:", self.source_lang)
        
        # Target language
        self.target_lang = QComboBox()
        self.target_lang.addItems(["English (en)", "Ukrainian (uk)", "Polish (pl)", "German (de)", "French (fr)", "Spanish (es)"])
        current_target = self.current_settings.get('target_lang', 'en')
        target_index = self._get_target_lang_index(current_target)
        self.target_lang.setCurrentIndex(target_index)
        translate_layout.addRow("Target Language:", self.target_lang)
        
        translate_group.setLayout(translate_layout)
        layout.addWidget(translate_group)
    
    def _create_tts_settings(self, layout):
        """Create settings UI for TTS service."""
        # TTS settings group
        tts_group = QGroupBox("Text-to-Speech Configuration")
        tts_layout = QFormLayout()
        
        # Voice selection
        self.voice_selection = QComboBox()
        self.voice_selection.addItems(["Default", "Female", "Male", "Fast", "High Quality"])
        current_voice = self.current_settings.get('tts_voice', 'Default')
        voice_index = self.voice_selection.findText(current_voice)
        if voice_index >= 0:
            self.voice_selection.setCurrentIndex(voice_index)
        tts_layout.addRow("Voice:", self.voice_selection)
        
        # Speed
        self.speech_speed = QSpinBox()
        self.speech_speed.setRange(50, 200)
        self.speech_speed.setValue(self.current_settings.get('speech_speed', 100))
        self.speech_speed.setSuffix("%")
        tts_layout.addRow("Speed:", self.speech_speed)
        
        tts_group.setLayout(tts_layout)
        layout.addWidget(tts_group)
    
    def _create_capture_settings(self, layout):
        """Create settings UI for Capture service."""
        # Capture settings group
        capture_group = QGroupBox("Audio Capture Configuration")
        capture_layout = QFormLayout()
        
        # Sample rate
        self.sample_rate = QSpinBox()
        self.sample_rate.setRange(8000, 48000)
        self.sample_rate.setValue(self.current_settings.get('sample_rate', 16000))
        self.sample_rate.setSuffix(" Hz")
        capture_layout.addRow("Sample Rate:", self.sample_rate)
        
        # Input device (would be populated dynamically in real implementation)
        self.input_device = QComboBox()
        self.input_device.addItems(["Default", "Microphone 1", "Microphone 2", "Virtual Input"])
        current_device = self.current_settings.get('input_device', 'Default')
        device_index = self.input_device.findText(current_device)
        if device_index >= 0:
            self.input_device.setCurrentIndex(device_index)
        capture_layout.addRow("Input Device:", self.input_device)
        
        capture_group.setLayout(capture_layout)
        layout.addWidget(capture_group)
    
    def _create_playback_settings(self, layout):
        """Create settings UI for Playback service."""
        # Playback settings group
        playback_group = QGroupBox("Audio Playback Configuration")
        playback_layout = QFormLayout()
        
        # Output device
        self.output_device = QComboBox()
        self.output_device.addItems(["Default", "Speakers", "Headphones", "Virtual Output"])
        current_device = self.current_settings.get('output_device', 'Default')
        device_index = self.output_device.findText(current_device)
        if device_index >= 0:
            self.output_device.setCurrentIndex(device_index)
        playback_layout.addRow("Output Device:", self.output_device)
        
        # Volume
        self.volume_level = QSpinBox()
        self.volume_level.setRange(0, 100)
        self.volume_level.setValue(self.current_settings.get('volume', 80))
        self.volume_level.setSuffix("%")
        playback_layout.addRow("Volume:", self.volume_level)
        
        playback_group.setLayout(playback_layout)
        layout.addWidget(playback_group)
    
    def _get_lang_index(self, lang_code):
        """Get the index for a language code in the source language combo."""
        lang_map = {
            'auto': 0,
            'uk': 1,
            'pl': 2,
            'en': 3,
            'de': 4,
            'fr': 5,
            'es': 6
        }
        return lang_map.get(lang_code, 0)
    
    def _get_target_lang_index(self, lang_code):
        """Get the index for a language code in the target language combo."""
        lang_map = {
            'en': 0,
            'uk': 1,
            'pl': 2,
            'de': 3,
            'fr': 4,
            'es': 5
        }
        return lang_map.get(lang_code, 0)
    
    def _on_wyoming_enabled_changed(self, checked):
        """Enable or disable Wyoming host and port fields."""
        self.wyoming_host.setEnabled(checked)
        self.wyoming_port.setEnabled(checked)
        
    def accept(self):
        """Save settings and close dialog."""
        settings = {}
        
        if self.service_name == 'whisper':
            settings = {
                'use_wyoming': self.wyoming_enabled.isChecked(),
                'wyoming_host': self.wyoming_host.text(),
                'wyoming_port': self.wyoming_port.value(),
            }
        elif self.service_name == 'translate':
            settings = {
                'source_lang': self._get_selected_source_lang(),
                'target_lang': self._get_selected_target_lang()
            }
        elif self.service_name == 'tts':
            settings = {
                'tts_voice': self.voice_selection.currentText(),
                'speech_speed': self.speech_speed.value()
            }
        elif self.service_name == 'capture':
            settings = {
                'sample_rate': self.sample_rate.value(),
                'input_device': self.input_device.currentText()
            }
        elif self.service_name == 'playback':
            settings = {
                'output_device': self.output_device.currentText(),
                'volume': self.volume_level.value()
            }
        
        self.settings_changed.emit(self.service_name, settings)
        super().accept()
        
    def _get_selected_source_lang(self):
        """Get the selected source language code."""
        index = self.source_lang.currentIndex()
        lang_codes = ['auto', 'uk', 'pl', 'en', 'de', 'fr', 'es']
        return lang_codes[index] if 0 <= index < len(lang_codes) else 'auto'
        
    def _get_selected_target_lang(self):
        """Get the selected target language code."""
        index = self.target_lang.currentIndex()
        lang_codes = ['en', 'uk', 'pl', 'de', 'fr', 'es']
        return lang_codes[index] if 0 <= index < len(lang_codes) else 'en'
    
    def _load_current_config(self):
        """Load current configuration from the config manager."""
        try:
            from ...core.config import get_config_manager
            config_manager = get_config_manager()
            
            if self.service_name == "whisper":
                wyoming_config = config_manager.get_wyoming_config()
                self.wyoming_enabled.setChecked(wyoming_config["use_wyoming"])
                self.wyoming_host.setText(wyoming_config["host"])
                self.wyoming_port.setValue(wyoming_config["port"])
                    
            elif self.service_name == "translate":
                # Load translation settings
                source_lang = config_manager.get("translation.source_lang", "auto")
                target_lang = config_manager.get("translation.target_lang", "en")
                
                source_index = self._get_lang_index(source_lang)
                self.source_lang.setCurrentIndex(source_index)
                
                target_index = self._get_target_lang_index(target_lang)
                self.target_lang.setCurrentIndex(target_index)
                
            elif self.service_name == "tts":
                # Load TTS settings
                tts_voice = config_manager.get("tts.voice", "Default")
                speech_speed = config_manager.get("tts.speech_speed", 100)
                
                voice_index = self.voice_selection.findText(tts_voice)
                if voice_index >= 0:
                    self.voice_selection.setCurrentIndex(voice_index)
                
                self.speech_speed.setValue(speech_speed)
                
            elif self.service_name == "capture":
                # Load capture settings
                sample_rate = config_manager.get("audio.sample_rate", 16000)
                input_device = config_manager.get("audio.input_device", "Default")
                
                self.sample_rate.setValue(sample_rate)
                
                device_index = self.input_device.findText(input_device)
                if device_index >= 0:
                    self.input_device.setCurrentIndex(device_index)
                    
            elif self.service_name == "playback":
                # Load playback settings
                output_device = config_manager.get("audio.output_device", "Default")
                volume = config_manager.get("audio.volume", 80)
                
                device_index = self.output_device.findText(output_device)
                if device_index >= 0:
                    self.output_device.setCurrentIndex(device_index)
                
                self.volume_level.setValue(volume)
                
        except Exception as e:
            logger.error(f"Error loading configuration for {self.service_name}: {e}")
