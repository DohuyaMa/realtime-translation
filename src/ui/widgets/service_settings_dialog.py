"""Service-specific settings dialog for the real-time translation system."""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, 
    QGroupBox, QCheckBox, QPushButton, QLineEdit,
    QLabel, QComboBox, QSpinBox, QDialogButtonBox,
    QMessageBox
)
from PySide6.QtCore import Qt, Signal
from loguru import logger
import sys


class ServiceSettingsDialog(QDialog):
    """Service-specific settings dialog."""
    
    settings_changed = Signal(str, dict)  # service_name, settings
    
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
        # Wyoming service settings group
        wyoming_group = QGroupBox("Wyoming ASR Configuration")
        wyoming_layout = QFormLayout()
        
        # Wyoming service enabled
        self.wyoming_enabled = QCheckBox("Use Wyoming ASR Service")
        self.wyoming_enabled.setChecked(self.current_settings.get('use_wyoming', False))
        wyoming_layout.addRow("Enable Wyoming:", self.wyoming_enabled)
        
        # Wyoming host
        self.wyoming_host = QLineEdit()
        self.wyoming_host.setText(self.current_settings.get('wyoming_host', 'localhost'))
        wyoming_layout.addRow("Wyoming Host:", self.wyoming_host)
        
        # Wyoming port
        self.wyoming_port = QSpinBox()
        self.wyoming_port.setRange(1, 65535)
        self.wyoming_port.setValue(self.current_settings.get('wyoming_port', 10300))
        wyoming_layout.addRow("Wyoming Port:", self.wyoming_port)
        
        wyoming_group.setLayout(wyoming_layout)
        layout.addWidget(wyoming_group)
        
        # Model settings
        model_group = QGroupBox("Model Configuration")
        model_layout = QFormLayout()
        
        self.model_type = QComboBox()
        self.model_type.addItems(["tiny", "base", "small", "medium", "large"])
        current_model = self.current_settings.get('whisper_model', 'medium')
        model_index = self.model_type.findText(current_model)
        if model_index >= 0:
            self.model_type.setCurrentIndex(model_index)
        model_layout.addRow("Model:", self.model_type)
        
        model_group.setLayout(model_layout)
        layout.addWidget(model_group)
        
        # Connect Wyoming enabled to host/port controls
        self.wyoming_enabled.stateChanged.connect(self._on_wyoming_enabled_changed)
        self._on_wyoming_enabled_changed(self.wyoming_enabled.isChecked())
    
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
                'whisper_model': self.model_type.currentText()
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
                # Load Wyoming settings
                wyoming_config = config_manager.get_wyoming_config()
                self.wyoming_enabled.setChecked(wyoming_config["use_wyoming"])
                self.wyoming_host.setText(wyoming_config["host"])
                self.wyoming_port.setValue(wyoming_config["port"])
                
                # Load model settings
                whisper_model = config_manager.get("translation.whisper_model", "medium")
                model_index = self.model_type.findText(whisper_model)
                if model_index >= 0:
                    self.model_type.setCurrentIndex(model_index)
                    
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
