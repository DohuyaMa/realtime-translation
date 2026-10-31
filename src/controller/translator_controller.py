"""Concrete implementation of the TranslatorController using adapters."""

from typing import Callable, Dict, List, Optional

from .controller import TranslatorController, Device
from ..adapters.direct_adapter import DirectAdapter


class ConcreteTranslatorController(TranslatorController):
    """Concrete implementation of the TranslatorController using an adapter."""

    def __init__(self, adapter: DirectAdapter):
        """Initialize the controller with an adapter."""
        self._adapter = adapter

    def start_pipeline(self) -> bool:
        """Start the entire translation pipeline."""
        return self._adapter.start_pipeline()

    def stop_pipeline(self) -> bool:
        """Stop the entire translation pipeline."""
        return self._adapter.stop_pipeline()

    def refresh_services(self) -> Dict:
        """Force a health poll of the remote speech services."""
        return self._adapter.refresh_services()

    def reconfigure_services(self, settings) -> bool:
        """Apply new EdgeSettings (endpoints/mode/segmentation)."""
        return self._adapter.reconfigure_services(settings)

    def get_status(self) -> Dict:
        """Get system status."""
        return self._adapter.get_status()

    def set_event_callback(self, cb: Optional[Callable[[Dict], None]]) -> None:
        """Subscribe to pipeline status events (from pipeline threads)."""
        self._adapter.set_event_callback(cb)

    def set_languages(self, source_lang: str, target_lang: str = "en") -> bool:
        """Set source and target languages."""
        return self._adapter.set_languages(source_lang, target_lang)

    def get_input_devices(self) -> List[Device]:
        """Get available input devices."""
        return self._adapter.get_input_devices()

    def get_output_devices(self) -> List[Device]:
        """Get available output devices."""
        return self._adapter.get_output_devices()

    def set_input_device(self, device_id: str) -> bool:
        """Set the input device."""
        return self._adapter.set_input_device(device_id)

    def set_output_device(self, device_id: str) -> bool:
        """Set the output device."""
        return self._adapter.set_output_device(device_id)

    def get_audio_levels(self) -> Dict[str, float]:
        """Get current audio input/output levels."""
        return self._adapter.get_audio_levels()

    def toggle_translation(self, enabled: bool) -> bool:
        """Enable or disable translation."""
        return self._adapter.toggle_translation(enabled)

    def cleanup(self) -> None:
        """Clean up resources."""
        self._adapter.cleanup()
