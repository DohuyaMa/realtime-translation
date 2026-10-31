"""Controller interface for the real-time translation system."""

from typing import Callable, Dict, List, Optional, Protocol


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

    def standby(self) -> List[str]:
        """Kill switch: stop everything, unload this host's models; errors."""
        ...

    def resume(self) -> List[str]:
        """Undo standby: load this host's models, resume; errors."""
        ...

    def in_standby(self) -> bool:
        """True between standby() and resume()."""
        ...

    def refresh_services(self) -> Dict:
        """Force a health poll of the remote speech services."""
        ...

    def reconfigure_services(self, settings) -> bool:
        """Apply new EdgeSettings (endpoints/mode/segmentation)."""
        ...

    def get_status(self) -> Dict:
        """Get system status."""
        ...

    def set_event_callback(self, cb: Optional[Callable[[Dict], None]]) -> None:
        """Subscribe to pipeline status events (from pipeline threads)."""
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
