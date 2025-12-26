"""Service status panel widget for the real-time translation system."""
from PySide6.QtWidgets import (
    QWidget, QGroupBox, QGridLayout, QLabel,
    QPushButton, QVBoxLayout
)
from PySide6.QtCore import Signal as pyqtSignal
from typing import Dict, List


class ServiceStatusPanel(QGroupBox):
    """Widget for displaying and controlling service status."""
    
    # Signal emitted when a service control button is clicked
    service_control_requested = pyqtSignal(str, bool)  # service_name, should_start
    # Signal emitted when a service settings button is clicked
    service_settings_requested = pyqtSignal(str)  # service_name
    
    def __init__(self, parent=None):
        super().__init__("Service Status", parent)
        
        self._services = {}
        self._status_labels = {}
        self._control_buttons = {}
        
        self.init_ui()
    
    def init_ui(self):
        """Initialize the service status panel UI."""
        layout = QGridLayout(self)
        
        # Define services with their display names
        self._services = {
            "capture": "Capture Service",
            "whisper": "Whisper Service", 
            "translate": "Translation Service",
            "tts": "TTS Service",
            "playback": "Playback Service"
        }
        
        # Create UI elements for each service
        for idx, (service_key, service_name) in enumerate(self._services.items()):
            # Status indicator
            status_label = QLabel("●")
            status_label.setStyleSheet("color: red; font-size: 16px; font-weight: bold;")
            status_label.setToolTip(f"{service_name} status")
            layout.addWidget(status_label, idx, 0)
            self._status_labels[f"{service_key}_status"] = status_label
            
            # Service name
            name_label = QLabel(service_name)
            layout.addWidget(name_label, idx, 1)
            
            # Status text
            status_text = QLabel("Not Connected")
            status_text.setStyleSheet("color: gray;")
            layout.addWidget(status_text, idx, 2)
            self._status_labels[f"{service_key}_text"] = status_text
            
            # Settings button
            settings_btn = QPushButton(f"Set {self._services[service_key].replace(' Service', '')}")
            settings_btn.clicked.connect(
                lambda checked, s=service_key: self._on_service_settings_clicked(s)
            )
            layout.addWidget(settings_btn, idx, 3)
            
            # Control button
            control_btn = QPushButton("Start")
            control_btn.clicked.connect(
                lambda checked, s=service_key: self._on_service_control_clicked(s)
            )
            layout.addWidget(control_btn, idx, 4)
            self._control_buttons[service_key] = control_btn
    
    def _on_service_control_clicked(self, service_name: str):
        """Handle service control button click."""
        button = self._control_buttons[service_name]
        should_start = button.text() == "Start"
        self.service_control_requested.emit(service_name, should_start)
    
    def _on_service_settings_clicked(self, service_name: str):
        """Handle service settings button click."""
        self.service_settings_requested.emit(service_name)
    
    def update_service_status(self, service_name: str, connected: bool, can_control: bool = True):
        """Update the status of a specific service."""
        if service_name not in self._services:
            return
        
        # Update status indicator
        status_label = self._status_labels[f"{service_name}_status"]
        status_text = self._status_labels[f"{service_name}_text"]
        
        if connected:
            status_label.setStyleSheet("color: green; font-size: 16px; font-weight: bold;")
            status_text.setText("Connected")
            status_text.setStyleSheet("color: green;")
        else:
            status_label.setStyleSheet("color: red; font-size: 16px; font-weight: bold;")
            status_text.setText("Not Connected")
            status_text.setStyleSheet("color: gray;")
    
    def update_service_control(self, service_name: str, is_running: bool):
        """Update the control button state for a service."""
        if service_name not in self._services:
            return
        
        button = self._control_buttons[service_name]
        button.setText("Stop" if is_running else "Start")
    
    def update_all_services(self, status_data: Dict):
        """Update all services based on status data."""
        for service_name in self._services.keys():
            connected = status_data.get(f'{service_name}_connected', False)
            self.update_service_status(service_name, connected)
            
            # Determine if service is running based on button state or status
            is_running = connected  # Simplified logic - in real app, this might be more complex
            self.update_service_control(service_name, is_running)
    
    def get_service_names(self) -> List[str]:
        """Get list of all service names."""
        return list(self._services.keys())


class ServiceStatusManager:
    """Manager for handling service status updates."""
    
    def __init__(self, panel: ServiceStatusPanel):
        self.panel = panel
    
    def update_status(self, status_data: Dict):
        """Update the service status panel with new status data."""
        self.panel.update_all_services(status_data)