"""Runtime configuration module for managing dynamic socket paths and directories.

This module provides centralized configuration for socket paths and directories
that comply with XDG standards and can be controlled by environment variables.
"""

import os
from pathlib import Path
from typing import Optional


class RuntimeConfig:
    """Centralized runtime configuration for socket paths and directories."""
    
    def __init__(self, base_dir: Optional[str] = None):
        """
        Initialize runtime configuration.
        
        Args:
            base_dir: Optional base directory override. If not provided, uses XDG-compliant temp directory.
        """
        self._base_dir = base_dir or self._get_default_base_dir()
        self._ensure_base_dir_exists()
    
    @staticmethod
    def _get_default_base_dir() -> str:
        """Get the default base directory following XDG standards.
        
        Returns:
            Path to the runtime directory, prioritizing:
            1. RT_RUNTIME_DIR environment variable if set
            2. XDG_RUNTIME_DIR/rt if XDG_RUNTIME_DIR is set
            3. /tmp/rt-{user} as fallback
        """
        # Check for explicit runtime directory
        if 'RT_RUNTIME_DIR' in os.environ:
            return os.environ['RT_RUNTIME_DIR']
        
        # Check for XDG runtime directory
        xdg_runtime_dir = os.environ.get('XDG_RUNTIME_DIR')
        if xdg_runtime_dir:
            xdg_rt_dir = Path(xdg_runtime_dir) / 'rt'
            return str(xdg_rt_dir)
        
        # Fallback to /tmp with user-specific directory
        user = os.environ.get('USER', 'unknown')
        return f'/tmp/rt-{user}'
    
    def _ensure_base_dir_exists(self):
        """Ensure the base directory exists, creating it if necessary."""
        Path(self._base_dir).mkdir(parents=True, exist_ok=True)
        # Set appropriate permissions for runtime directory
        os.chmod(self._base_dir, 0o700)
    
    @property
    def base_dir(self) -> str:
        """Get the base runtime directory."""
        return self._base_dir
    
    def get_socket_path(self, service_name: str) -> str:
        """Get the socket path for a specific service.
        
        Args:
            service_name: Name of the service (e.g., 'capture', 'playback', 'tts', etc.)
            
        Returns:
            Path to the socket file
        """
        return os.path.join(self.base_dir, f'rt-{service_name}.sock')
    
    def get_capture_socket_path(self) -> str:
        """Get the socket path for the capture service."""
        return self.get_socket_path('capture')
    
    def get_playback_socket_path(self) -> str:
        """Get the socket path for the playback service."""
        return self.get_socket_path('playback')
    
    def get_tts_socket_path(self) -> str:
        """Get the socket path for the TTS service."""
        return self.get_socket_path('tts')
    
    def get_translate_socket_path(self) -> str:
        """Get the socket path for the translation service."""
        return self.get_socket_path('translate')
    
    def get_whisper_socket_path(self) -> str:
        """Get the socket path for the whisper service."""
        return self.get_socket_path('whisper')
    
    def get_hybrid_whisper_socket_path(self) -> str:
        """Get the socket path for the hybrid whisper service."""
        return self.get_socket_path('hybrid-whisper')
    
    def get_main_socket_path(self) -> str:
        """Get the socket path for the main service."""
        return self.get_socket_path('main')


# Global instance for convenience
_runtime_config: Optional[RuntimeConfig] = None


def get_runtime_config() -> RuntimeConfig:
    """Get the global runtime configuration instance.
    
    Returns:
        RuntimeConfig instance
    """
    global _runtime_config
    if _runtime_config is None:
        _runtime_config = RuntimeConfig()
    return _runtime_config


def reset_runtime_config():
    """Reset the global runtime configuration instance (for testing purposes)."""
    global _runtime_config
    _runtime_config = None