from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QComboBox, QPushButton, QLabel, QProgressBar,
    QCheckBox, QGroupBox, QSystemTrayIcon, QMenu
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QIcon, QAction
import sys
from loguru import logger
from typing import Optional, Dict
import json

from ..translation_system import TranslationSystem

class MainWindow(QMainWindow):
    """Main window for the real-time translation application."""
    
    def __init__(self):
        super().__init__()
        
        # Translation system
        self.translation_system: Optional[TranslationSystem] = None
        
        # UI state
        self.is_translating = False
        self.update_timer: Optional[QTimer] = None
        
        self.init_ui()
        self.setup_translation_system()
        self.setup_tray_icon()
        
    def init_ui(self):
        """Initialize the user interface."""
        self.setWindowTitle('Real-Time Translator')
        self.setMinimumWidth(600)
        
        # Create central widget and layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        
        # Language selection
        lang_group = QGroupBox("Language Settings")
        lang_layout = QVBoxLayout()
        
        # Source language
        source_layout = QHBoxLayout()
        source_layout.addWidget(QLabel("Source Language:"))
        self.source_lang_combo = QComboBox()
        self.source_lang_combo.addItems(["Auto", "Ukrainian (uk)", "Polish (pl)"])
        self.source_lang_combo.currentTextChanged.connect(self.on_language_changed)
        source_layout.addWidget(self.source_lang_combo)
        lang_layout.addLayout(source_layout)
        
        # Target language (fixed to English)
        target_layout = QHBoxLayout()
        target_layout.addWidget(QLabel("Target Language:"))
        target_label = QLabel("English (en)")
        target_layout.addWidget(target_label)
        lang_layout.addLayout(target_layout)
        
        lang_group.setLayout(lang_layout)
        layout.addWidget(lang_group)
        
        # Audio devices
        audio_group = QGroupBox("Audio Devices")
        audio_layout = QVBoxLayout()
        
        # Input device
        input_layout = QHBoxLayout()
        input_layout.addWidget(QLabel("Input Device:"))
        self.input_device_combo = QComboBox()
        input_layout.addWidget(self.input_device_combo)
        audio_layout.addLayout(input_layout)
        
        # Output device
        output_layout = QHBoxLayout()
        output_layout.addWidget(QLabel("Output Device:"))
        self.output_device_combo = QComboBox()
        output_layout.addWidget(self.output_device_combo)
        audio_layout.addLayout(output_layout)
        
        audio_group.setLayout(audio_layout)
        layout.addWidget(audio_group)
        
        # Audio levels
        levels_group = QGroupBox("Audio Levels")
        levels_layout = QVBoxLayout()
        
        # Input level
        input_level_layout = QHBoxLayout()
        input_level_layout.addWidget(QLabel("Input:"))
        self.input_level = QProgressBar()
        self.input_level.setMinimum(0)
        self.input_level.setMaximum(100)
        input_level_layout.addWidget(self.input_level)
        levels_layout.addLayout(input_level_layout)
        
        # Output level
        output_level_layout = QHBoxLayout()
        output_level_layout.addWidget(QLabel("Output:"))
        self.output_level = QProgressBar()
        self.output_level.setMinimum(0)
        self.output_level.setMaximum(100)
        output_level_layout.addWidget(self.output_level)
        levels_layout.addLayout(output_level_layout)
        
        levels_group.setLayout(levels_layout)
        layout.addWidget(levels_group)
        
        # Status and controls
        status_layout = QHBoxLayout()
        
        # Translation toggle
        self.translate_button = QPushButton("Start Translation")
        self.translate_button.clicked.connect(self.toggle_translation)
        status_layout.addWidget(self.translate_button)
        
        # Always on top
        self.always_on_top = QCheckBox("Always on Top")
        self.always_on_top.stateChanged.connect(self.toggle_always_on_top)
        status_layout.addWidget(self.always_on_top)
        
        # Minimize to tray
        self.minimize_to_tray = QCheckBox("Minimize to Tray")
        self.minimize_to_tray.setChecked(True)
        status_layout.addWidget(self.minimize_to_tray)
        
        layout.addLayout(status_layout)
        
        # Status bar
        self.statusBar().showMessage('Ready')
        
        # Set up update timer
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.update_ui)
        self.update_timer.start(100)  # Update every 100ms
        
    def setup_translation_system(self):
        """Initialize the translation system."""
        try:
            self.translation_system = TranslationSystem(
                use_virtual_audio=True
            )
            self.translation_system.set_status_callback(self.handle_status_update)
            
            # Populate audio devices
            self.refresh_audio_devices()
            
        except Exception as e:
            logger.error(f"Failed to initialize translation system: {e}")
            self.statusBar().showMessage('Failed to initialize translation system')
            
    def setup_tray_icon(self):
        """Set up system tray icon and menu."""
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(QIcon.fromTheme("audio-input-microphone"))
        
        # Create tray menu
        tray_menu = QMenu()
        
        show_action = QAction("Show", self)
        show_action.triggered.connect(self.show)
        tray_menu.addAction(show_action)
        
        toggle_action = QAction("Start Translation", self)
        toggle_action.triggered.connect(self.toggle_translation)
        tray_menu.addAction(toggle_action)
        
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.quit_application)
        tray_menu.addAction(quit_action)
        
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.show()
        
    def refresh_audio_devices(self):
        """Refresh the audio device lists."""
        if not self.translation_system:
            return
            
        devices = self.translation_system.get_audio_devices()
        
        self.input_device_combo.clear()
        self.output_device_combo.clear()
        
        for device in devices['inputs']:
            self.input_device_combo.addItem(device['description'], device['name'])
            
        for device in devices['outputs']:
            self.output_device_combo.addItem(device['description'], device['name'])
            
    def toggle_translation(self):
        """Toggle translation on/off."""
        if not self.translation_system:
            return
            
        self.is_translating = not self.is_translating
        
        if self.is_translating:
            self.translation_system.start()
            self.translate_button.setText("Stop Translation")
            self.statusBar().showMessage('Translation active')
        else:
            self.translation_system.stop()
            self.translate_button.setText("Start Translation")
            self.statusBar().showMessage('Translation stopped')
            
    def toggle_always_on_top(self, state):
        """Toggle always-on-top window state."""
        flags = self.windowFlags()
        if state:
            flags |= Qt.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()
        
    def on_language_changed(self, text):
        """Handle language selection change."""
        if not self.translation_system:
            return
            
        # Extract language code from combo box text
        lang_code = text.split('(')[-1].strip(')')
        if lang_code.lower() == 'auto':
            lang_code = 'auto'
            
        self.translation_system.set_languages(lang_code)
        
    def update_ui(self):
        """Update UI elements with current status."""
        if not self.translation_system:
            return
            
        # Get current stats
        stats = self.translation_system.get_stats()
        
        # Update audio levels
        self.input_level.setValue(int(stats.get('audio_level', 0) * 100))
        
        # Update status message
        if stats.get('is_speech', False):
            self.statusBar().showMessage('Speech detected')
            
    def handle_status_update(self, status: Dict):
        """Handle status updates from translation system."""
        status_type = status.get('status')
        
        if status_type == 'recognition_complete':
            text = status.get('text', '')
            self.statusBar().showMessage(f'Recognized: {text}')
            
        elif status_type == 'synthesis_complete':
            duration = status.get('duration', 0)
            self.statusBar().showMessage(f'Speech synthesized ({duration:.1f}s)')
            
    def closeEvent(self, event):
        """Handle window close event."""
        if self.minimize_to_tray.isChecked():
            event.ignore()
            self.hide()
        else:
            self.quit_application()
            
    def quit_application(self):
        """Clean up and quit the application."""
        if self.translation_system:
            self.translation_system.cleanup()
        self.tray_icon.hide()
        sys.exit(0)