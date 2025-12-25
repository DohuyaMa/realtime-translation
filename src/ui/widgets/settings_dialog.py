"""Settings dialog for configuring Wyoming services and other application settings."""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, 
    QGroupBox, QCheckBox, QPushButton, QLineEdit,
    QLabel, QComboBox, QSpinBox, QDialogButtonBox
)
from PySide6.QtCore import Qt, Signal
from loguru import logger
import sys


class SettingsDialog(QDialog):
    """Settings dialog for configuring Wyoming services and other application settings."""
    
    settings_changed = Signal(dict)
    
    def __init__(self, current_settings=None, parent=None):
        super().__init__(parent)
        self.current_settings = current_settings or {}
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.setMinimumWidth(500)
        
        self.init_ui()
        
    def init_ui(self):
        """Initialize the settings UI."""
        layout = QVBoxLayout(self)
        
        # Wyoming service settings group
        wyoming_group = QGroupBox("Wyoming Service Configuration")
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
        
        # Audio settings group
        audio_group = QGroupBox("Audio Settings")
        audio_layout = QFormLayout()
        
        # Sample rate
        self.sample_rate = QSpinBox()
        self.sample_rate.setRange(8000, 48000)
        self.sample_rate.setValue(self.current_settings.get('sample_rate', 16000))
        self.sample_rate.setSuffix(" Hz")
        audio_layout.addRow("Sample Rate:", self.sample_rate)
        
        audio_group.setLayout(audio_layout)
        layout.addWidget(audio_group)
        
        # Translation settings group
        translation_group = QGroupBox("Translation Settings")
        translation_layout = QFormLayout()
        
        # Source language
        self.source_lang = QComboBox()
        self.source_lang.addItems(["Auto", "Ukrainian (uk)", "Polish (pl)", "English (en)", "German (de)", "French (fr)", "Spanish (es)"])
        current_source = self.current_settings.get('source_lang', 'auto')
        source_index = self._get_lang_index(current_source)
        self.source_lang.setCurrentIndex(source_index)
        translation_layout.addRow("Source Language:", self.source_lang)
        
        # Target language
        self.target_lang = QComboBox()
        self.target_lang.addItems(["English (en)", "Ukrainian (uk)", "Polish (pl)", "German (de)", "French (fr)", "Spanish (es)"])
        current_target = self.current_settings.get('target_lang', 'en')
        target_index = self._get_target_lang_index(current_target)
        self.target_lang.setCurrentIndex(target_index)
        translation_layout.addRow("Target Language:", self.target_lang)
        
        translation_group.setLayout(translation_layout)
        layout.addWidget(translation_group)
        
        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        
        # Connect Wyoming enabled to host/port controls
        self.wyoming_enabled.stateChanged.connect(self._on_wyoming_enabled_changed)
        self._on_wyoming_enabled_changed(self.wyoming_enabled.isChecked())
        
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
        settings = {
            'use_wyoming': self.wyoming_enabled.isChecked(),
            'wyoming_host': self.wyoming_host.text(),
            'wyoming_port': self.wyoming_port.value(),
            'sample_rate': self.sample_rate.value(),
            'source_lang': self._get_selected_source_lang(),
            'target_lang': self._get_selected_target_lang()
        }
        
        # Save settings to configuration manager
        from ...core.config import get_config_manager
        config_manager = get_config_manager()
        
        # Update Wyoming settings
        config_manager.set_wyoming_config(
            use_wyoming=settings['use_wyoming'],
            host=settings['wyoming_host'],
            port=settings['wyoming_port']
        )
        
        # Update audio settings
        config_manager.set('audio.sample_rate', settings['sample_rate'])
        
        # Update translation settings
        config_manager.set('translation.source_lang', settings['source_lang'])
        config_manager.set('translation.target_lang', settings['target_lang'])
        
        # Save to file
        config_manager.save()
        
        self.settings_changed.emit(settings)
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


class SettingsButton(QPushButton):
    """Button to open the settings dialog."""
    
    settings_applied = Signal(dict)
    
    def __init__(self, parent=None):
        super().__init__("Settings", parent)
        self.clicked.connect(self.open_settings)
        
    def open_settings(self):
        """Open the settings dialog."""
        # Get current settings from parent or controller
        current_settings = getattr(self.parent(), 'current_settings', {})
        
        dialog = SettingsDialog(current_settings, self.parent())
        dialog.settings_changed.connect(self.on_settings_changed)
        dialog.exec()
        
    def on_settings_changed(self, settings):
        """Handle settings change."""
        self.settings_applied.emit(settings)