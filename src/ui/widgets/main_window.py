"""Refactored main window for the real-time translation application using controller pattern."""
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QComboBox, QPushButton, QLabel, QProgressBar,
    QCheckBox, QGroupBox, QSystemTrayIcon, QMenu,
    QFrame, QSplitter, QApplication
)
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QAction
import sys
from loguru import logger
from typing import Optional, Dict

from ..controller import UIController
from ...controller.translator_controller import ConcreteTranslatorController
from ...adapters import DirectAdapter
from .status_logger import StatusLogger, StatusManager
from .service_status_panel import ServiceStatusPanel
from .settings_dialog import SettingsButton


class MainWindow(QMainWindow):
    """Main window for the real-time translation application using controller pattern."""
    _update_signal = Signal(object)

    def __init__(self, controller: Optional[UIController] = None):
        super().__init__()
        
        # UI controller (abstraction over backend)
        self.ui_controller: Optional[UIController] = controller
        
        # Status manager for separating status display from logging
        self.status_logger = StatusLogger()
        self.status_manager = StatusManager(self.status_logger)
        
        # Service status panel
        self.service_status_panel = ServiceStatusPanel()
        
        # UI state
        self.is_translating = False
        self.update_timer: Optional[QTimer] = None
        
        # Wyoming settings
        self._current_use_wyoming = False
        self._current_wyoming_host = "localhost"
        self._current_wyoming_port = 10300
        
        # Connect UI controller callbacks
        self._update_signal.connect(self._apply_ui_update)
        self.ui_controller.set_status_callback(self.handle_status_update)
        self.ui_controller.set_update_callback(self.update_ui_from_controller)
        
        self.init_ui()
        self.setup_tray_icon()
        self.refresh_device_lists()
        
        self.service_status_panel.service_control_requested.connect(self.on_service_control_requested)
        self.service_status_panel.service_settings_requested.connect(self.on_service_settings_requested)
        self.ui_controller.start_event_polling(interval=0.5)
        
    def init_ui(self):
        """Initialize the user interface."""
        self.setWindowTitle('Real-Time Translator')
        self.setMinimumWidth(800)
        self.setMinimumHeight(700)  # Increased to accommodate status logger
        
        # Create central widget and main layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        
        # Create a splitter to separate main controls from status logger
        main_splitter = QSplitter(Qt.Orientation.Vertical)
        
        # Upper part: main controls
        upper_widget = QWidget()
        upper_layout = QVBoxLayout(upper_widget)
        
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
        upper_layout.addWidget(lang_group)
        
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
        upper_layout.addWidget(audio_group)
        
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
        upper_layout.addWidget(levels_group)
        
        # Service status panel
        upper_layout.addWidget(self.service_status_panel)
        # Status and controls
        status_layout = QHBoxLayout()
        
        # Translation toggle
        self.translate_button = QPushButton("Start Translation")
        self.translate_button.clicked.connect(self.toggle_translation)
        status_layout.addWidget(self.translate_button)
        
        # Settings button
        self.settings_button = SettingsButton()
        self.settings_button.settings_applied.connect(self.on_general_settings_changed)
        status_layout.addWidget(self.settings_button)
        
        # Always on top
        self.always_on_top = QCheckBox("Always on Top")
        self.always_on_top.stateChanged.connect(self.toggle_always_on_top)
        status_layout.addWidget(self.always_on_top)
        
        # Minimize to tray
        self.minimize_to_tray = QCheckBox("Minimize to Tray")
        self.minimize_to_tray.setChecked(True)
        status_layout.addWidget(self.minimize_to_tray)
        
        upper_layout.addLayout(status_layout)
        
        # Add upper widget to splitter
        main_splitter.addWidget(upper_widget)
        
        # Lower part: status logger
        main_splitter.addWidget(self.status_logger)
        
        # Set initial sizes (give more space to controls, less to logger)
        main_splitter.setSizes([500, 200])
        
        main_layout.addWidget(main_splitter)
        
        # Set up update timer (reduced frequency as per context recommendations)
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.fallback_update)
        self.update_timer.start(1000)  # Update every 1000ms as fallback only
    
    def fallback_update(self):
        """Fallback update method in case event-based updates fail."""
        # This is just a fallback; primary updates come from the controller
        pass
    
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
        
    def refresh_device_lists(self):
        """Refresh the audio device lists from the controller."""
        try:
            # Get input devices
            input_devices = self.ui_controller.get_input_devices()
            
            # Store current selections to restore them if possible
            current_input = self.input_device_combo.currentText()
            
            self.input_device_combo.clear()
            for device in input_devices:
                self.input_device_combo.addItem(device['description'], device['name'])
            
            # Restore previous selection if it still exists
            if current_input:
                index = self.input_device_combo.findText(current_input)
                if index >= 0:
                    self.input_device_combo.setCurrentIndex(index)
            
            # Get output devices
            output_devices = self.ui_controller.get_output_devices()
            
            # Store current selections to restore them if possible
            current_output = self.output_device_combo.currentText()
            
            self.output_device_combo.clear()
            for device in output_devices:
                self.output_device_combo.addItem(device['description'], device['name'])
            
            # Restore previous selection if it still exists
            if current_output:
                index = self.output_device_combo.findText(current_output)
                if index >= 0:
                    self.output_device_combo.setCurrentIndex(index)
            
            # Connect the input device selection to a handler
            self.input_device_combo.currentTextChanged.connect(self.on_input_device_changed)
        except Exception as e:
            logger.error(f"Error refreshing device lists: {e}")
            self.status_manager.log_error(f"Error refreshing device lists: {e}")
        
    def toggle_translation(self):
        """Toggle translation on/off."""
        if not self.ui_controller:
            return
            
        success = self.ui_controller.toggle_pipeline()
        if success:
            # Update the button text and status based on current state
            status = self.ui_controller.get_status()
            is_running = status.get('running', False)
            
            if is_running:
                self.translate_button.setText("Stop Translation")
                self.status_manager.set_status('Translation active')
                self.status_manager.log_info('Translation pipeline started')
                self.is_translating = True
            else:
                self.translate_button.setText("Start Translation")
                self.status_manager.set_status('Translation stopped')
                self.status_manager.log_info('Translation pipeline stopped')
                self.is_translating = False
            
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
        if not self.ui_controller:
            return
            
        # Extract language code from combo box text
        lang_code = text.split('(')[-1].strip(')')
        if lang_code.lower() == 'auto':
            lang_code = 'auto'
            
        self.ui_controller.set_languages(lang_code)
    
    def on_input_device_changed(self, text):
        """Handle input device selection change."""
        if not self.ui_controller or not text:
            return
            
        # Find the device name that corresponds to the selected description
        input_devices = self.ui_controller.get_input_devices()
        selected_device_name = None
        
        for device in input_devices:
            if device['description'] == text:
                selected_device_name = device['name']
                break
        
        if selected_device_name:
            try:
                success = self.ui_controller.set_input_device(selected_device_name)
                if success:
                    self.status_manager.set_status(f'Input device changed to: {text}')
                    self.status_manager.log_info(f'Input device changed to: {text}')
                else:
                    self.status_manager.set_status(f'Failed to change input device to: {text}')
                    self.status_manager.log_error(f'Failed to change input device to: {text}')
            except Exception as e:
                logger.error(f"Failed to change input device: {e}")
                self.status_manager.set_status(f"Failed to change input device: {e}")
                self.status_manager.log_error(f"Failed to change input device: {e}")
    
    def update_ui(self):
        """Update UI elements with current status - called by timer."""
        # This is the fallback polling approach, but we also update from controller events
        if self.ui_controller:
            self.ui_controller.update_ui()
    
    def update_ui_from_controller(self, update_data: Dict):
        """Called from UIController polling thread — marshals to main thread via signal."""
        self._update_signal.emit(update_data)

    def _apply_ui_update(self, update_data: Dict):
        """Apply UI update — always runs on the main Qt thread."""
        # Audio level
        input_level = int(update_data.get('input', 0) * 100)
        self.input_level.setValue(input_level)

        # Recognized / translated text panels
        for text in update_data.get('pending_recognized', []):
            self.status_manager.log_recognized(text)
        for text in update_data.get('pending_translated', []):
            self.status_manager.log_translated(text)

        # Service status indicators
        from .service_status_panel import ServiceStatusManager
        ServiceStatusManager(self.service_status_panel).update_status(update_data)
    
    def on_service_control_requested(self, service_name: str, should_start: bool):
        """Handle service control requests from the service status panel."""
        if not self.ui_controller:
            return
        
        try:
            if should_start:
                success = self.ui_controller.start_service(service_name)
                if success:
                    self.status_manager.log_info(f'{service_name.capitalize()} service started')
                else:
                    self.status_manager.log_error(f'Failed to start {service_name} service')
            else:
                success = self.ui_controller.stop_service(service_name)
                if success:
                    self.status_manager.log_info(f'{service_name.capitalize()} service stopped')
                else:
                    self.status_manager.log_error(f'Failed to stop {service_name} service')
        except Exception as e:
            logger.error(f"Failed to control {service_name} service: {e}")
            self.status_manager.log_error(f"Error controlling {service_name} service: {e}")
    
    def on_service_settings_requested(self, service_name: str):
        """Handle service settings requests from the service status panel."""
        logger.info(f"Service settings requested for: {service_name}")
        
        # Create service-specific settings dialog
        from .service_settings_dialog import ServiceSettingsDialog
        current_settings = {}
        
        if service_name == 'whisper':
            current_settings = {
                'use_wyoming': getattr(self, '_current_use_wyoming', False),
                'wyoming_host': getattr(self, '_current_wyoming_host', 'localhost'),
                'wyoming_port': getattr(self, '_current_wyoming_port', 10300)
            }
        elif service_name == 'translate':
            current_settings = {
                'source_lang': getattr(self, '_current_source_lang', 'auto'),
                'target_lang': getattr(self, '_current_target_lang', 'en')
            }
        elif service_name == 'tts':
            current_settings = {
                'tts_voice': getattr(self, '_current_tts_voice', 'Default'),
                'speech_speed': getattr(self, '_current_speech_speed', 100)
            }
        elif service_name == 'capture':
            current_settings = {
                'sample_rate': getattr(self, '_current_sample_rate', 16000),
                'input_device': getattr(self, '_current_input_device', 'Default')
            }
        elif service_name == 'playback':
            current_settings = {
                'output_device': getattr(self, '_current_output_device', 'Default'),
                'volume': getattr(self, '_current_volume', 80)
            }
        
        dialog = ServiceSettingsDialog(service_name, current_settings, self)
        dialog.settings_changed.connect(self.on_service_specific_settings_changed)
        dialog.exec()
    
    def on_service_specific_settings_changed(self, service_name: str, settings: dict):
        """Handle service-specific settings changes."""
        logger.info(f"Service settings changed for {service_name}: {settings}")
        
        # Store the settings for future reference
        for key, value in settings.items():
            setattr(self, f'_current_{service_name}_{key}', value)
        
        # Apply settings using the UI controller
        success = self.ui_controller.update_service_config(service_name, settings)
        
        if success:
            self.status_manager.log_info(f"Settings applied for {service_name} service")
            
            # Store the settings for future reference
            for key, value in settings.items():
                setattr(self, f'_current_{service_name}_{key}', value)
                
            # Special handling for certain settings
            if service_name == 'translate':
                # Update language combo boxes
                source_lang = settings.get('source_lang', 'auto')
                self._update_language_combo(self.source_lang_combo, source_lang)
        else:
            self.status_manager.log_error(f"Failed to apply settings for {service_name} service")
        
    def on_general_settings_changed(self, settings):
        """Handle general settings changes from the settings dialog."""
        logger.info(f"General settings changed: {settings}")
        
        # Store current settings for future reference
        self.current_settings = settings
        
        # Check if Wyoming settings have changed
        use_wyoming = settings.get('use_wyoming', False)
        wyoming_host = settings.get('wyoming_host', 'localhost')
        wyoming_port = settings.get('wyoming_port', 10300)
        
        wyoming_changed = (
            hasattr(self, '_current_use_wyoming') and
            (self._current_use_wyoming != use_wyoming or
             self._current_wyoming_host != wyoming_host or
             self._current_wyoming_port != wyoming_port)
        )
        
        # Store current Wyoming settings
        self._current_use_wyoming = use_wyoming
        self._current_wyoming_host = wyoming_host
        self._current_wyoming_port = wyoming_port
        
        # If Wyoming settings have changed, we need to reconfigure the controller
        if wyoming_changed:
            # Stop the current pipeline if it's running
            if self.is_translating:
                self.ui_controller.stop_pipeline()
                self.translate_button.setText("Start Translation")
                self.is_translating = False
            
            # Reconfigure the controller with new Wyoming settings
            success = self.ui_controller.reconfigure_controller(
                use_wyoming=use_wyoming,
                wyoming_host=wyoming_host,
                wyoming_port=wyoming_port
            )
            
            if success:
                self.status_manager.log_info("Wyoming configuration updated successfully")
                
                # If we were translating before, start the pipeline again with new settings
                if self.is_translating:
                    self.ui_controller.start_pipeline()
            else:
                self.status_manager.log_error("Failed to update Wyoming configuration")
                return
        else:
            # Apply language settings only if Wyoming settings didn't change
            source_lang = settings.get('source_lang', 'auto')
            target_lang = settings.get('target_lang', 'en')
            self.ui_controller.set_languages(source_lang, target_lang)
            
            # Update language combo boxes
            self._update_language_combo(self.source_lang_combo, source_lang)
        
        # Update UI elements to reflect new settings
        # Update UI elements to reflect new settings
        self.status_manager.log_info("Settings applied successfully")
    
    def on_settings_changed(self, settings):
        """Handle settings changes from the settings dialog - legacy method."""
        # This is now an alias to the new method
        self.on_general_settings_changed(settings)
        
    def _update_language_combo(self, combo, lang_code):
        """Update a language combo box to show the correct language."""
        lang_display_map = {
            'auto': 'Auto',
            'uk': 'Ukrainian (uk)',
            'pl': 'Polish (pl)',
            'en': 'English (en)',
            'de': 'German (de)',
            'fr': 'French (fr)',
            'es': 'Spanish (es)'
        }
        display_text = lang_display_map.get(lang_code, 'Auto')
        index = combo.findText(display_text)
        if index >= 0:
            combo.setCurrentIndex(index)
        
    def handle_status_update(self, message: str):
        """Handle status updates from UI controller."""
        # Use the status manager to separate status display from logging
        self.status_manager.set_status(message)
        self.status_manager.log_info(message)
    def closeEvent(self, event):
        """Handle window close event."""
        if self.minimize_to_tray.isChecked():
            event.ignore()
            self.hide()
        else:
            self.quit_application()
            
    def quit_application(self):
        """Clean up and quit the application."""
        if self.ui_controller:
            # Stop event polling before cleanup
            self.ui_controller.stop_event_polling()
            self.ui_controller.cleanup()
        if hasattr(self, 'tray_icon'):
            self.tray_icon.hide()
        # Use QApplication.quit() instead of sys.exit(0) as per context recommendations
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.quit()
        else:
            # Fallback - though this should not happen in normal operation
            import sys
            sys.exit(0)