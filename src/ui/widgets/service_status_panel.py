"""Service status panel widget for the real-time translation system."""
from PySide6.QtWidgets import (
    QWidget, QGroupBox, QGridLayout, QLabel,
    QPushButton, QVBoxLayout, QHBoxLayout
)
from PySide6.QtCore import Signal as pyqtSignal
from typing import Dict, List


class ServiceStatusPanel(QGroupBox):
    """Widget for displaying and controlling service status."""

    service_control_requested = pyqtSignal(str, bool)   # service_name, should_start
    service_restart_requested = pyqtSignal(str)          # service_name
    service_settings_requested = pyqtSignal(str)         # service_name
    restart_all_requested = pyqtSignal()
    reconnect_ipc_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__("Service Status", parent)

        self._services = {}
        self._status_labels = {}
        self._control_buttons = {}
        self._restart_buttons = {}

        self.init_ui()

    def init_ui(self):
        outer = QVBoxLayout(self)

        grid = QGridLayout()
        outer.addLayout(grid)

        self._services = {
            "capture":        "Capture",
            "whisper":        "Whisper",
            "translate":      "Translate",
            "tts":            "TTS",
            "playback":       "Playback",
            "wyoming":        "Wyoming STT",
        }

        for idx, (key, label) in enumerate(self._services.items()):
            # status dot
            dot = QLabel("●")
            dot.setStyleSheet("color: red; font-size: 16px; font-weight: bold;")
            grid.addWidget(dot, idx, 0)
            self._status_labels[f"{key}_status"] = dot

            # service name
            grid.addWidget(QLabel(label), idx, 1)

            # status text
            txt = QLabel("Offline")
            txt.setStyleSheet("color: gray;")
            grid.addWidget(txt, idx, 2)
            self._status_labels[f"{key}_text"] = txt

            # Start/Stop button
            ctrl_btn = QPushButton("Start")
            ctrl_btn.setFixedWidth(54)
            ctrl_btn.clicked.connect(
                lambda _, s=key: self._on_control_clicked(s)
            )
            grid.addWidget(ctrl_btn, idx, 3)
            self._control_buttons[key] = ctrl_btn

            # Restart button
            rst_btn = QPushButton("Restart")
            rst_btn.setFixedWidth(60)
            rst_btn.clicked.connect(
                lambda _, s=key: self.service_restart_requested.emit(s)
            )
            grid.addWidget(rst_btn, idx, 4)
            self._restart_buttons[key] = rst_btn

            # Settings button
            set_btn = QPushButton("Settings")
            set_btn.setFixedWidth(64)
            set_btn.clicked.connect(
                lambda _, s=key: self.service_settings_requested.emit(s)
            )
            grid.addWidget(set_btn, idx, 5)

        # Bottom action bar
        action_bar = QHBoxLayout()
        outer.addLayout(action_bar)

        restart_all_btn = QPushButton("Restart All Services")
        restart_all_btn.setToolTip("Stop and restart every pipeline service")
        restart_all_btn.clicked.connect(self.restart_all_requested.emit)
        action_bar.addWidget(restart_all_btn)

        reconnect_btn = QPushButton("Reconnect IPC")
        reconnect_btn.setToolTip("Force-reconnect IPC clients (use after service restarts)")
        reconnect_btn.clicked.connect(self.reconnect_ipc_requested.emit)
        action_bar.addWidget(reconnect_btn)

        action_bar.addStretch()

    # ------------------------------------------------------------------

    def _on_control_clicked(self, service_name: str):
        btn = self._control_buttons[service_name]
        self.service_control_requested.emit(service_name, btn.text() == "Start")

    def update_service_status(self, service_name: str, connected: bool, can_control: bool = True):
        if service_name not in self._services:
            return
        dot = self._status_labels[f"{service_name}_status"]
        txt = self._status_labels[f"{service_name}_text"]
        if connected:
            dot.setStyleSheet("color: green; font-size: 16px; font-weight: bold;")
            txt.setText("Running")
            txt.setStyleSheet("color: green;")
        else:
            dot.setStyleSheet("color: red; font-size: 16px; font-weight: bold;")
            txt.setText("Offline")
            txt.setStyleSheet("color: gray;")

    def update_service_control(self, service_name: str, is_running: bool):
        if service_name not in self._services:
            return
        self._control_buttons[service_name].setText("Stop" if is_running else "Start")

    def update_all_services(self, status_data: Dict):
        for key in self._services:
            connected = status_data.get(f'{key}_connected', False)
            self.update_service_status(key, connected)
            self.update_service_control(key, connected)

    def get_service_names(self) -> List[str]:
        return list(self._services.keys())


class ServiceStatusManager:
    def __init__(self, panel: ServiceStatusPanel):
        self.panel = panel

    def update_status(self, status_data: Dict):
        self.panel.update_all_services(status_data)
