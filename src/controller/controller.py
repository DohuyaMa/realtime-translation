"""Controller interface for the real-time translation system."""
from typing import Protocol, Dict, Optional, List
import abc


class Device:
    """Represents an audio device."""
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description


class TranslatorController(Protocol):
    """Abstract controller interface for the translation system."""
    
    def start_pipeline(self) -> bool:
        """Start the entire translation pipeline."""
        ...
    
    def stop_pipeline(self) -> bool:
        """Stop the entire translation pipeline."""
        ...
    
    def start_service(self, name: str) -> bool:
        """Start a specific service."""
        ...
    
    def stop_service(self, name: str) -> bool:
        """Stop a specific service."""
        ...
    
    def get_status(self) -> Dict:
        """Get system status."""
        ...
    
    def set_languages(self, source_lang: str, target_lang: str = "en") -> bool:
        """Set source and target languages."""
        ...
    
    def get_input_devices(self) -> List[Device]:
        """Get available input devices."""
        ...
    
    def get_output_devices(self) -> List[Device]:
        """Get available output devices."""
        ...
    
    def set_input_device(self, device_id: str) -> bool:
        """Set the input device."""
        ...
    
    def set_output_device(self, device_id: str) -> bool:
        """Set the output device."""
        ...
    
    def get_audio_levels(self) -> Dict[str, float]:
        """Get current audio input/output levels."""
        ...
    
    def toggle_translation(self, enabled: bool) -> bool:
        """Enable or disable translation."""
        ...
    
    def cleanup(self) -> None:
        """Clean up resources."""
        ...