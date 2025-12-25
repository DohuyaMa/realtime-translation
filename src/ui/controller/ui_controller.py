"""UI Controller for managing UI-specific logic and connecting to the backend controller."""
from typing import Callable, Dict, Optional
from loguru import logger
import threading
import time

from ...controller.translator_controller import ConcreteTranslatorController
from ...controller.controller import Device


class UIController:
    """UI Controller for managing UI-specific logic and connecting to the backend controller."""
    
    def __init__(self, controller: ConcreteTranslatorController):
        """Initialize the UI controller with a backend controller."""
        self._controller = controller
        self._status_callback: Optional[Callable] = None
        self._update_callback: Optional[Callable] = None
        self._event_callbacks: Dict[str, Callable] = {}
        self._last_status = {}
        self._polling_thread = None
        self._polling_active = False
        # Store original adapter to allow reconfiguration
        self._original_controller = controller
        
    def set_status_callback(self, callback: Callable):
        """Set callback for status updates."""
        self._status_callback = callback
        
    def set_update_callback(self, callback: Callable):
        """Set callback for UI updates."""
        self._update_callback = callback
    
    def register_event_callback(self, event_type: str, callback: Callable):
        """Register a callback for a specific event type."""
        self._event_callbacks[event_type] = callback
    
    def start_event_polling(self, interval: float = 0.5):
        """Start a background thread to poll for status changes and trigger events."""
        if self._polling_active:
            return
            
        self._polling_active = True
        self._polling_thread = threading.Thread(target=self._poll_for_changes, args=(interval,), daemon=True)
        self._polling_thread.start()
    
    def stop_event_polling(self):
        """Stop the background polling thread."""
        self._polling_active = False
        if self._polling_thread:
            self._polling_thread.join(timeout=1.0)  # Wait up to 1 second for thread to finish
    
    def _poll_for_changes(self, interval: float):
        """Background thread function to poll for changes and trigger events."""
        while self._polling_active:
            try:
                current_status = self._controller.get_status()
                
                # Compare with last status to detect changes
                if current_status != self._last_status:
                    # Trigger update callback with the new status
                    if self._update_callback:
                        audio_levels = self._controller.get_audio_levels()
                        update_data = {
                            **current_status,
                            **audio_levels
                        }
                        self._update_callback(update_data)
                    
                    # Store the new status
                    self._last_status = current_status
                
                time.sleep(interval)
            except Exception as e:
                logger.error(f"Error in polling thread: {e}")
                time.sleep(interval)
    
    def start_pipeline(self) -> bool:
        """Start the translation pipeline."""
        result = self._controller.start_pipeline()
        if result and self._status_callback:
            self._status_callback('Pipeline started')
        return result
    
    def stop_pipeline(self) -> bool:
        """Stop the translation pipeline."""
        result = self._controller.stop_pipeline()
        if result and self._status_callback:
            self._status_callback('Pipeline stopped')
        return result
    
    def toggle_pipeline(self) -> bool:
        """Toggle the pipeline on/off."""
        # Get current status to determine if we should start or stop
        status = self._controller.get_status()
        is_running = status.get('running', False)
        
        if is_running:
            return self.stop_pipeline()
        else:
            return self.start_pipeline()
    
    def start_service(self, service_name: str) -> bool:
        """Start a specific service."""
        result = self._controller.start_service(service_name)
        if result and self._status_callback:
            self._status_callback(f'{service_name.capitalize()} service started')
        return result
    
    def stop_service(self, service_name: str) -> bool:
        """Stop a specific service."""
        result = self._controller.stop_service(service_name)
        if result and self._status_callback:
            self._status_callback(f'{service_name.capitalize()} service stopped')
        return result
    
    def toggle_service(self, service_name: str) -> bool:
        """Toggle a specific service on/off."""
        # Get current status to determine if we should start or stop
        status = self._controller.get_status()
        is_connected = status.get(f'{service_name}_connected', False)
        
        if is_connected:
            return self.stop_service(service_name)
        else:
            return self.start_service(service_name)
    
    def set_languages(self, source_lang: str, target_lang: str = "en") -> bool:
        """Set source and target languages."""
        result = self._controller.set_languages(source_lang, target_lang)
        if result and self._status_callback:
            self._status_callback(f'Languages set: {source_lang} -> {target_lang}')
        return result
    
    def get_input_devices(self) -> list:
        """Get available input devices."""
        try:
            devices = self._controller.get_input_devices()
            # Convert Device objects to dictionaries for UI
            return [{'name': d.name, 'description': d.description} for d in devices]
        except Exception as e:
            logger.error(f"Error getting input devices: {e}")
            return []
    
    def get_output_devices(self) -> list:
        """Get available output devices."""
        try:
            devices = self._controller.get_output_devices()
            # Convert Device objects to dictionaries for UI
            return [{'name': d.name, 'description': d.description} for d in devices]
        except Exception as e:
            logger.error(f"Error getting output devices: {e}")
            return []
    
    def set_input_device(self, device_id: str) -> bool:
        """Set the input device."""
        result = self._controller.set_input_device(device_id)
        if result and self._status_callback:
            self._status_callback(f'Input device set to: {device_id}')
        return result
    
    def set_output_device(self, device_id: str) -> bool:
        """Set the output device."""
        result = self._controller.set_output_device(device_id)
        if result and self._status_callback:
            self._status_callback(f'Output device set to: {device_id}')
        return result
    
    def get_audio_levels(self) -> Dict[str, float]:
        """Get current audio levels."""
        return self._controller.get_audio_levels()
    
    def get_status(self) -> Dict:
        """Get system status."""
        return self._controller.get_status()
    
    def toggle_translation(self, enabled: bool) -> bool:
        """Toggle translation on/off."""
        result = self._controller.toggle_translation(enabled)
        if result and self._status_callback:
            self._status_callback(f'Translation {"enabled" if enabled else "disabled"}')
        return result
    
    def update_ui(self):
        """Update UI with current status - for polling approach as fallback."""
        if self._update_callback:
            status = self._controller.get_status()
            audio_levels = self._controller.get_audio_levels()
            
            # Combine status and audio levels for UI update
            update_data = {
                **status,
                **audio_levels
            }
            self._update_callback(update_data)
    
    def reconfigure_controller(self, use_wyoming=False, wyoming_host="localhost", wyoming_port=10300):
        """Reconfigure the controller with new Wyoming settings."""
        try:
            # Check if the current adapter supports reconfiguration
            current_adapter = self._controller._adapter
            
            if hasattr(current_adapter, 'reconfigure_wyoming'):
                # Use the reconfigure method if available
                success = current_adapter.reconfigure_wyoming(
                    use_wyoming=use_wyoming,
                    wyoming_host=wyoming_host,
                    wyoming_port=wyoming_port
                )
                return success
            else:
                # Fallback to creating a new controller (original behavior)
                # Get current status before reconfiguring
                current_status = self._controller.get_status()
                was_running = current_status.get('running', False)
                
                # Stop the current pipeline if it's running
                if was_running:
                    self._controller.stop_pipeline()
                
                # Get the current languages
                source_lang = current_status.get('source_language', 'auto')
                target_lang = current_status.get('target_language', 'en')
                
                # Create a new adapter with the Wyoming settings
                from ...adapters.direct_adapter import DirectAdapter
                new_adapter = DirectAdapter(
                    source_lang=source_lang,
                    target_lang=target_lang,
                    use_wyoming=use_wyoming,
                    wyoming_host=wyoming_host,
                    wyoming_port=wyoming_port
                )
                
                # Create a new controller with the new adapter
                from ...controller.translator_controller import ConcreteTranslatorController
                new_controller = ConcreteTranslatorController(new_adapter)
                
                # Replace the current controller
                old_controller = self._controller
                self._controller = new_controller
                
                # If the pipeline was running, start it again with new settings
                if was_running:
                    self._controller.start_pipeline()
                
                # Clean up the old controller
                old_controller.cleanup()
                
                return True
        except Exception as e:
            logger.error(f"Error reconfiguring controller: {e}")
            return False
    
    def cleanup(self):
        """Clean up resources."""
        self._controller.cleanup()