"""Configuration management for the real-time translation system."""
import os
import yaml
from typing import Dict, Any, Optional
from loguru import logger
from pathlib import Path


class ConfigManager:
    """Configuration manager for storing and retrieving application settings."""
    
    def __init__(self, config_dir: Optional[str] = None):
        """Initialize the configuration manager.
        
        Args:
            config_dir: Directory to store config files. If None, uses default location.
        """
        if config_dir is None:
            self.config_dir = Path.home() / ".config" / "real-time-translator"
        else:
            self.config_dir = Path(config_dir)
        
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.config_dir / "config.yml"
        
        # Default configuration
        self._default_config = {
            'audio': {
                'sample_rate': 16000,
                'chunk_size': 1024,
                'use_virtual_devices': True
            },
            'translation': {
                'source_lang': 'auto',
                'target_lang': 'en',
                'whisper_model': 'medium'
            },
            'wyoming': {
                'use_wyoming': False,
                'host': 'localhost',
                'port': 10300
            },
            'ui': {
                'minimize_to_tray': True,
                'start_minimized': False
            },
            'models': {
                'whisper': {
                    'backend': 'local',
                    'model': 'medium',
                    'device': 'cuda',
                    'compute_type': 'float16',
                    'beam_size': 5,
                    'temperature': 0.0,
                    'initial_prompt': '',
                },
                'translate': {
                    'model': 'Helsinki-NLP/opus-mt-uk-en',
                    'device': 'cuda',
                    'num_beams': 4,
                    'repetition_penalty': 1.2,
                    'max_length': 200,
                },
                'tts': {
                    'engine': 'kokoro',
                    'model': 'hexgrad/Kokoro-82M',
                    'voice': 'af_heart',
                    'device': 'cuda',
                    'speed': 1.0,
                },
            }
        }
        
        # Load configuration
        self._config = self._load_config()
    
    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from file, with defaults for missing values."""
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r') as f:
                    file_config = yaml.safe_load(f) or {}
                
                # Merge with defaults to ensure all keys exist
                config = self._merge_config(self._default_config, file_config)
                return config
            except Exception as e:
                logger.error(f"Error loading config file, using defaults: {e}")
                return self._default_config.copy()
        else:
            return self._default_config.copy()
    
    def _merge_config(self, default: Dict, override: Dict) -> Dict:
        """Recursively merge two configuration dictionaries."""
        result = default.copy()
        
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._merge_config(result[key], value)
            else:
                result[key] = value
        
        return result
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value using dot notation (e.g., 'wyoming.host').
        
        Args:
            key: Configuration key using dot notation
            default: Default value if key doesn't exist
            
        Returns:
            Configuration value or default
        """
        keys = key.split('.')
        value = self._config
        
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        
        return value
    
    def set(self, key: str, value: Any):
        """Set a configuration value using dot notation (e.g., 'wyoming.host').
        
        Args:
            key: Configuration key using dot notation
            value: Value to set
        """
        keys = key.split('.')
        config = self._config
        
        # Navigate to the parent of the target key
        for k in keys[:-1]:
            if k not in config or not isinstance(config[k], dict):
                config[k] = {}
            config = config[k]
        
        # Set the final value
        config[keys[-1]] = value
    
    def get_all(self) -> Dict[str, Any]:
        """Get the entire configuration dictionary.
        
        Returns:
            Complete configuration dictionary
        """
        return self._config.copy()
    
    def save(self):
        """Save the current configuration to file."""
        try:
            with open(self.config_file, 'w') as f:
                yaml.dump(self._config, f, default_flow_style=False)
            logger.info(f"Configuration saved to {self.config_file}")
        except Exception as e:
            logger.error(f"Error saving config file: {e}")
    
    def reset_to_defaults(self):
        """Reset configuration to default values."""
        self._config = self._default_config.copy()
        self.save()
    
    def get_wyoming_config(self) -> Dict[str, Any]:
        """Get Wyoming-specific configuration.
        
        Returns:
            Dictionary with Wyoming configuration
        """
        return {
            'use_wyoming': self.get('wyoming.use_wyoming', False),
            'host': self.get('wyoming.host', 'localhost'),
            'port': self.get('wyoming.port', 10300)
        }
    
    def set_wyoming_config(self, use_wyoming: bool, host: str = "localhost", port: int = 10300):
        """Set Wyoming-specific configuration.
        
        Args:
            use_wyoming: Whether to use Wyoming services
            host: Wyoming service host
            port: Wyoming service port
        """
        self.set('wyoming.use_wyoming', use_wyoming)
        self.set('wyoming.host', host)
        self.set('wyoming.port', port)
        self.save()
    
    def get_audio_config(self) -> Dict[str, Any]:
        """Get audio-specific configuration.
        
        Returns:
            Dictionary with audio configuration
        """
        return {
            'sample_rate': self.get('audio.sample_rate', 16000),
            'chunk_size': self.get('audio.chunk_size', 1024),
            'use_virtual_devices': self.get('audio.use_virtual_devices', True)
        }
    
    def get_translation_config(self) -> Dict[str, Any]:
        """Get translation-specific configuration.
        
        Returns:
            Dictionary with translation configuration
        """
        return {
            'source_lang': self.get('translation.source_lang', 'auto'),
            'target_lang': self.get('translation.target_lang', 'en'),
            'whisper_model': self.get('translation.whisper_model', 'medium')
        }


# Global configuration instance
_config_manager: Optional[ConfigManager] = None


def get_config_manager() -> ConfigManager:
    """Get the global configuration manager instance.
    
    Returns:
        Configuration manager instance
    """
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager()
    return _config_manager


def init_config_manager(config_dir: Optional[str] = None) -> ConfigManager:
    """Initialize the global configuration manager instance.
    
    Args:
        config_dir: Directory to store config files. If None, uses default location.
        
    Returns:
        Configuration manager instance
    """
    global _config_manager
    _config_manager = ConfigManager(config_dir)
    return _config_manager