"""Direct adapter that wraps the existing TranslationSystem."""
from typing import Dict, List
from loguru import logger

from ..translation_system import TranslationSystem
from ..controller.controller import Device
from ..core.preflight.pipewire import PipeWirePreflight
from ..core.env import setup_ml_env


class DirectAdapter:
    """Direct adapter that wraps the existing TranslationSystem implementation."""
    
    def __init__(self, source_lang: str = "auto", target_lang: str = "en", sample_rate: int = 16000, skip_preflight: bool = False, use_wyoming: bool = False, wyoming_host: str = "localhost", wyoming_port: int = 10300):
        """Initialize the DirectAdapter with a TranslationSystem instance."""
        # Check PipeWire availability before proceeding (unless explicitly skipped)
        if not skip_preflight:
            if not PipeWirePreflight.check():
                raise RuntimeError("PipeWire preflight check failed. Please ensure PipeWire virtual sinks are set up.")
        
        # Set up environment variables
        setup_ml_env()
        self.translation_system = TranslationSystem(
            source_lang=source_lang,
            target_lang=target_lang,
            sample_rate=sample_rate,
            use_wyoming=use_wyoming,
            wyoming_host=wyoming_host,
            wyoming_port=wyoming_port
        )
        
        # Store configuration for Wyoming service
        self.use_wyoming = use_wyoming
        self.wyoming_host = wyoming_host
        self.wyoming_port = wyoming_port
        
        # Store reference to audio router for direct device management in dev mode
        self.audio_router = self.translation_system.audio_router

        # Track which services have been "started" in devShell mode (no IPC clients)
        self._devshell_started: set = set()
    def reconfigure_wyoming(self, use_wyoming: bool, wyoming_host: str = "localhost", wyoming_port: int = 10300):
        """Reconfigure the adapter to use Wyoming services or local services."""
        try:
            # Store new Wyoming settings
            self.use_wyoming = use_wyoming
            self.wyoming_host = wyoming_host
            self.wyoming_port = wyoming_port
            
            # Get current pipeline status
            current_status = self.translation_system.get_stats()
            was_running = current_status.get('running', False)
            
            # Stop the current pipeline if it's running
            if was_running:
                self.translation_system.stop()
            
            # Store current language settings
            source_lang = self.translation_system.source_lang
            target_lang = self.translation_system.target_lang
            sample_rate = self.translation_system.sample_rate
            
            # Clean up the current translation system
            self.translation_system.cleanup()
            
            # Create a new translation system with the new Wyoming configuration
            # We need to update the socket path to use the appropriate whisper service
            from ..translation_system import TranslationSystem
            self.translation_system = TranslationSystem(
                source_lang=source_lang,
                target_lang=target_lang,
                sample_rate=sample_rate,
                use_wyoming=use_wyoming,
                wyoming_host=wyoming_host,
                wyoming_port=wyoming_port
            )
            
            # The TranslationSystem constructor handles the whisper client initialization
            # based on the use_wyoming parameter

            # Update audio_router reference to point to the new TranslationSystem's router
            self.audio_router = self.translation_system.audio_router

            # If the pipeline was running, start it again with new configuration
            if was_running:
                self.translation_system.start()

            logger.info(f"Wyoming reconfiguration completed. Now using Wyoming: {use_wyoming}")
            return True
        except Exception as e:
            logger.error(f"Failed to reconfigure Wyoming settings: {e}")
            return False

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
        try:
            # Check if the translation system has IPC clients available
            if (name == 'capture' and self.translation_system.capture_client is None) or \
               (name == 'whisper' and self.translation_system.whisper_client is None) or \
               (name == 'translate' and self.translation_system.translate_client is None) or \
               (name == 'tts' and self.translation_system.tts_client is None) or \
               (name == 'playback' and self.translation_system.playback_client is None):
                # In devShell mode, we may want to allow certain operations to continue
                # For services that can be started directly without IPC
                if name in ['capture', 'whisper', 'translate', 'tts', 'playback']:
                    logger.info(f"Service {name} not started (no IPC client in devShell mode)")
                    self._devshell_started.add(name)
                    return True
                else:
                    logger.warning(f"Cannot start {name} service: not recognized")
                    return False
            
            return self.translation_system.start_service(name)
        except Exception as e:
            logger.error(f"Failed to start {name} service: {e}")
            return False
    
    def stop_service(self, name: str) -> bool:
        """Stop a specific service."""
        try:
            # Check if the translation system has IPC clients available
            if (name == 'capture' and self.translation_system.capture_client is None) or \
               (name == 'whisper' and self.translation_system.whisper_client is None) or \
               (name == 'translate' and self.translation_system.translate_client is None) or \
               (name == 'tts' and self.translation_system.tts_client is None) or \
               (name == 'playback' and self.translation_system.playback_client is None):
                # In devShell mode, we may want to allow certain operations to continue
                # For services that can be stopped directly without IPC
                if name in ['capture', 'whisper', 'translate', 'tts', 'playback']:
                    logger.info(f"Service {name} not stopped (no IPC client in devShell mode)")
                    self._devshell_started.discard(name)
                    return True
                else:
                    logger.warning(f"Cannot stop {name} service: not recognized")
                    return False
            
            return self.translation_system.stop_service(name)
        except Exception as e:
            logger.error(f"Failed to stop {name} service: {e}")
            return False
    
    def get_status(self) -> Dict:
        """Get system status."""
        status = self.translation_system.get_stats()
        # In devShell mode all IPC clients are None; reflect manually-started services.
        devshell_mode = all(
            getattr(self.translation_system, f'{s}_client', None) is None
            for s in ('capture', 'whisper', 'translate', 'tts', 'playback')
        )
        if devshell_mode:
            for name in ('capture', 'whisper', 'translate', 'tts', 'playback'):
                status[f'{name}_connected'] = name in self._devshell_started
        return status
    
    def set_languages(self, source_lang: str, target_lang: str = "en") -> bool:
        """Set source and target languages."""
        try:
            # Check if the translation system has IPC clients available
            # If not, we can't set languages through the services
            if self.translation_system.whisper_client is None and self.translation_system.translate_client is None:
                logger.info(f"Setting languages in devShell mode: {source_lang} -> {target_lang}")
                # Still allow setting at the system level even if services are not available
                self.translation_system.source_lang = source_lang
                self.translation_system.target_lang = target_lang
                return True
            
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
        if not device_id or device_id.lower() == "default":
            return True   # placeholder — nothing to set
        try:
            if self.translation_system.capture_client is not None:
                result = self.translation_system.set_input_device(device_id)
                return result is not None
            if self.audio_router:
                self.audio_router.set_default_source(device_id)
                logger.info(f"Set input device directly: {device_id}")
                return True
            logger.error("Audio router not available")
            return False
        except Exception as e:
            logger.error(f"Failed to set input device: {e}")
            return False

    def set_output_device(self, device_id: str) -> bool:
        """Set the output device."""
        if not device_id or device_id.lower() == "default":
            return True   # placeholder — nothing to set
        try:
            if self.audio_router:
                self.audio_router.set_default_sink(device_id)
                logger.info(f"Set output device directly: {device_id}")
                return True
            logger.error("Audio router not available")
            return False
        except Exception as e:
            logger.error(f"Failed to set output device: {e}")
            return False
    
    def get_audio_levels(self) -> Dict[str, float]:
        """Get current audio input/output levels."""
        return {
            'input': self.translation_system._audio_level_input,
            'output': 0.0,
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