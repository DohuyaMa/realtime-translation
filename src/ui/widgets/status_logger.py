"""Status and logging component for the real-time translation system."""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, 
    QPushButton, QLabel, QSplitter
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QTextCursor
from typing import List, Tuple
import datetime
from loguru import logger


class StatusLogger(QWidget):
    """Widget for displaying status messages and logs separately from the main status bar."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._messages = []
        self._max_messages = 1000  # Limit to prevent memory issues
        
        self.init_ui()
    
    def init_ui(self):
        """Initialize the status logger UI."""
        layout = QVBoxLayout(self)
        
        # Create a splitter to separate status bar and log view
        splitter = QSplitter(Qt.Orientation.Vertical)
        
        # Status bar area (compact)
        self.status_area = QLabel()
        self.status_area.setStyleSheet(
            "QLabel { "
            "   border: 1px solid #cccccc; "
            "   border-radius: 4px; "
            "   padding: 4px; "
            "   background-color: #f0f0f0; "
            "} "
        )
        self.status_area.setText("Ready")
        self.status_area.setMinimumHeight(30)
        
        # Log view area (scrollable)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(1000)  # Limit displayed lines
        
        # Control buttons
        button_layout = QHBoxLayout()
        
        self.clear_button = QPushButton("Clear Log")
        self.clear_button.clicked.connect(self.clear_log)
        
        self.copy_button = QPushButton("Copy Log")
        self.copy_button.clicked.connect(self.copy_log)
        
        button_layout.addWidget(self.clear_button)
        button_layout.addWidget(self.copy_button)
        button_layout.addStretch()
        
        # Add to splitter
        splitter.addWidget(self.status_area)
        splitter.addWidget(self.log_view)
        
        # Set initial sizes to give more space to log
        splitter.setSizes([50, 450])
        
        layout.addWidget(splitter)
        layout.addLayout(button_layout)
    
    def set_status(self, message: str):
        """Set the status message in the status area."""
        self.status_area.setText(message)
    
    def add_log_message(self, message: str, level: str = "INFO"):
        """Add a message to the log view."""
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        formatted_message = f"[{timestamp}] {level}: {message}"
        
        # Add to log view
        self.log_view.moveCursor(QTextCursor.MoveOperation.End)
        self.log_view.insertPlainText(formatted_message + "\n")
        self.log_view.moveCursor(QTextCursor.MoveOperation.End)
        
        # Store in internal list as well (for potential external access)
        self._messages.append((timestamp, level, message))
        
        # Limit stored messages
        if len(self._messages) > self._max_messages:
            self._messages = self._messages[-self._max_messages:]
    
    def add_info(self, message: str):
        """Add an info message to the log."""
        self.add_log_message(message, "INFO")
    
    def add_warning(self, message: str):
        """Add a warning message to the log."""
        self.add_log_message(message, "WARNING")
    
    def add_error(self, message: str):
        """Add an error message to the log."""
        self.add_log_message(message, "ERROR")
    
    def add_debug(self, message: str):
        """Add a debug message to the log."""
        self.add_log_message(message, "DEBUG")
    
    def clear_log(self):
        """Clear the log view."""
        self.log_view.clear()
        self._messages.clear()
    
    def copy_log(self):
        """Copy the log content to clipboard."""
        clipboard = self.log_view.app().clipboard()
        clipboard.setText(self.log_view.toPlainText())
    
    def get_recent_messages(self, count: int = 10) -> List[Tuple[str, str, str]]:
        """Get the most recent messages."""
        return self._messages[-count:] if len(self._messages) >= count else self._messages


class StatusManager:
    """Manager for handling status updates and logging."""
    
    def __init__(self, status_logger: StatusLogger = None):
        self.status_logger = status_logger
        self._last_status = ""
    
    def set_status(self, message: str):
        """Set the current status."""
        self._last_status = message
        if self.status_logger:
            self.status_logger.set_status(message)
    
    def log_info(self, message: str):
        """Log an info message."""
        if self.status_logger:
            self.status_logger.add_info(message)
        logger.info(message)
    
    def log_warning(self, message: str):
        """Log a warning message."""
        if self.status_logger:
            self.status_logger.add_warning(message)
        logger.warning(message)
    
    def log_error(self, message: str):
        """Log an error message."""
        if self.status_logger:
            self.status_logger.add_error(message)
        logger.error(message)
    
    def log_debug(self, message: str):
        """Log a debug message."""
        if self.status_logger:
            self.status_logger.add_debug(message)
        logger.debug(message)