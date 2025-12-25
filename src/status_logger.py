"""Status and logging component for the real-time translation system services."""
from typing import List, Tuple
import datetime
from loguru import logger


class StatusLogger:
    """Component for storing and managing status messages and logs."""
    
    def __init__(self):
        self._messages = []
        self._max_messages = 1000  # Limit to prevent memory issues
    
    def add_log_message(self, message: str, level: str = "INFO"):
        """Add a message to the log."""
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        formatted_message = f"[{timestamp}] {level}: {message}"
        
        # Store in internal list
        self._messages.append((timestamp, level, message))
        
        # Limit stored messages
        if len(self._messages) > self._max_messages:
            self._messages = self._messages[-self._max_messages:]
        
        return formatted_message
    
    def add_info(self, message: str):
        """Add an info message to the log."""
        return self.add_log_message(message, "INFO")
    
    def add_warning(self, message: str):
        """Add a warning message to the log."""
        return self.add_log_message(message, "WARNING")
    
    def add_error(self, message: str):
        """Add an error message to the log."""
        return self.add_log_message(message, "ERROR")
    
    def add_debug(self, message: str):
        """Add a debug message to the log."""
        return self.add_log_message(message, "DEBUG")
    
    def get_recent_messages(self, count: int = 10) -> List[Tuple[str, str, str]]:
        """Get the most recent messages."""
        return self._messages[-count:] if len(self._messages) >= count else self._messages


class StatusManager:
    """Manager for handling status updates and logging."""
    
    def __init__(self, status_logger: StatusLogger = None):
        self.status_logger = status_logger or StatusLogger()
        self._last_status = ""
    
    def set_status(self, message: str):
        """Set the current status."""
        self._last_status = message
    
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