"""Direct adapter that wraps the existing TranslationSystem."""
from typing import Dict, List
from loguru import logger

from ..translation_system import TranslationSystem
from ..controller.controller import TranslatorController, Device


class DirectAdapter:
    """Direct adapter that wraps the existing TranslationSystem implementation."""
    
    def __init__(self, source_lang: str = "auto", target_lang: str = "en", sample_rate: int = 16000):
        """Initialize the DirectAdapter with a TranslationSystem instance."""
        self.translation_system = TranslationSystem(
            source_lang=source_lang,
            target_lang=target_lang,
            sample_rate=sample_rate
        )
    
    def start_pipeline(self) -> bool:
        """Start the entire translation pipeline."""
        try:
            self.translation_system.start()
            return True
        except Exception as e:
            logger.error(f"Failed to start pipeline: {e}")
            return False
    
    def stop_pipeline(self) -> bool:
        """Stop the entire translation pipeline."""
        try:
            self.translation_system.stop()
            return True
        except Exception as e:
            logger.error(f"Failed to stop pipeline: {e}")
            return False
    
    def start_service(self, name: str) -> bool:
        """Start a specific service."""
        return self.translation_system.start_service(name)
    
    def stop_service(self, name: str) -> bool:
        """Stop a specific service."""
        return self.translation_system.stop_service(name)
    
    def get_status(self) -> Dict:
        """Get system status."""
        return self.translation_system.get_stats()
    
    def set_languages(self, source_lang: str, target_lang: str = "en") -> bool:
        """Set source and target languages."""
        try:
            self.translation_system.set_languages(source_lang, target_lang)
            return True
        except Exception as e:
            logger.error(f"Failed to set languages: {e}")
            return False
    
    def get_input_devices(self) -> List[Device]:
        """Get available input devices."""
        devices = self.translation_system.get_audio_devices()
        input_devices = []
        for device_info in devices.get('inputs', []):
            # Assuming the device_info is a dict with 'name' and 'description' keys
            if isinstance(device_info, dict):
                input_devices.append(Device(device_info.get('name', ''), device_info.get('description', '')))
            else:
                # If it's not a dict, assume it's already a device dict with name and description
                input_devices.append(Device(str(device_info), str(device_info)))
        return input_devices
    
    def get_output_devices(self) -> List[Device]:
        """Get available output devices."""
        devices = self.translation_system.get_audio_devices()
        output_devices = []
        for device_info in devices.get('outputs', []):
            if isinstance(device_info, dict):
                output_devices.append(Device(device_info.get('name', ''), device_info.get('description', '')))
            else:
                output_devices.append(Device(str(device_info), str(device_info)))
        return output_devices
    
    def set_input_device(self, device_id: str) -> bool:
        """Set the input device."""
        try:
            result = self.translation_system.set_input_device(device_id)
            return result is not None
        except Exception as e:
            logger.error(f"Failed to set input device: {e}")
            return False
    
    def set_output_device(self, device_id: str) -> bool:
        """Set the output device."""
        # Currently, the TranslationSystem doesn't have a set_output_device method
        # We'll return False for now until this functionality is added
        logger.warning("set_output_device not implemented in TranslationSystem")
        return False
    
    def get_audio_levels(self) -> Dict[str, float]:
        """Get current audio input/output levels."""
        # Extract audio levels from the stats
        stats = self.translation_system.get_stats()
        return {
            'input': stats.get('audio_level', 0.0),
            'output': 0.0  # Output level not currently tracked in TranslationSystem
        }
    
    def toggle_translation(self, enabled: bool) -> bool:
        """Enable or disable translation."""
        try:
            self.translation_system.toggle_translation(enabled)
            return True
        except Exception as e:
            logger.error(f"Failed to toggle translation: {e}")
            return False
    
    def cleanup(self) -> None:
        """Clean up resources."""
        self.translation_system.cleanup()