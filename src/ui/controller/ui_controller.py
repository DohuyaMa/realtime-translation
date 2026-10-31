"""UI Controller for managing UI-specific logic and connecting to the backend controller."""
from typing import Callable, Dict, Optional, Tuple
from loguru import logger
import threading
import time

from ...controller.translator_controller import ConcreteTranslatorController
from ...controller.controller import Device

HEALTH_INTERVAL_S = 5.0
# the same pipeline problem repeats per phrase: log it again only after this
REPEAT_LOG_S = 10.0

SERVICE_NAMES = {"asr": "ASR", "tts": "TTS", "llm": "LLM"}


def describe_pipeline_event(event: Dict) -> Optional[Tuple[str, str]]:
    """(level, message) for the log widget, None for events it doesn't show.

    Pipeline problems that otherwise stay silent: service errors (incl. a
    model that is not loaded/ready - HTTP 503) and LLM replies rejected as
    not a translation."""
    status = event.get("status")
    if status == "translation_rejected":
        msg = (
            f"Переклад відкинуто ({event.get('reason') or '?'}):"
            f" «{event.get('original_text') or ''}»"
        )
        if event.get("output"):
            msg += f" → «{event['output']}»"
        return "WARN", msg
    if status == "error":
        service = SERVICE_NAMES.get(event.get("service"), event.get("service") or "?")
        kind = event.get("kind")
        if kind == "not_ready":
            return "ERROR", (
                f"{service}: модель не завантажена/не готова - фразу пропущено"
                " (стан моделі: панель «Сервіси» / Налаштування)"
            )
        if kind:
            return "ERROR", f"{service}: {kind} - фразу пропущено"
        return "ERROR", str(event.get("error") or f"{service}: помилка")
    return None


class PipelineEventLog:
    """Turns pipeline events into log lines; an identical line repeated
    within REPEAT_LOG_S is counted, not logged again."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._last: Dict[str, float] = {}
        self._repeats: Dict[str, int] = {}

    def lines(self, event: Dict) -> Optional[Tuple[str, str]]:
        described = describe_pipeline_event(event)
        if described is None:
            return None
        level, msg = described
        now = self._clock()
        last = self._last.get(msg)
        if last is not None and now - last < REPEAT_LOG_S:
            self._repeats[msg] = self._repeats.get(msg, 0) + 1
            return None
        self._last[msg] = now
        repeats = self._repeats.pop(msg, 0)
        if repeats:
            msg += f" (ще ×{repeats} за {REPEAT_LOG_S:.0f} с)"
        return level, msg


class UIController:
    """UI Controller for managing UI-specific logic and connecting to the backend controller."""
    
    def __init__(self, controller: ConcreteTranslatorController):
        """Initialize the UI controller with a backend controller."""
        self._controller = controller
        self._status_callback: Optional[Callable] = None
        self._update_callback: Optional[Callable] = None
        self._last_status = {}
        self._last_health: Dict = {}
        self._last_health_at = float('-inf')
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
    
    def set_pipeline_event_callback(self, callback: Optional[Callable[[Dict], None]]):
        """Pipeline status events (called from pipeline threads)."""
        self._controller.set_event_callback(callback)

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
        """Background thread function to poll and push all updates to the UI."""
        while self._polling_active:
            try:
                current_status = self._controller.get_status()
                now = time.monotonic()
                if now - self._last_health_at >= HEALTH_INTERVAL_S:
                    self._last_health_at = now
                    self._last_health = self.refresh_services()
                current_status = {**current_status, "health": self._last_health}
                # Always call the update callback so that:
                # - audio levels refresh every tick
                # - pending_recognized / pending_translated are never dropped
                if self._update_callback:
                    audio_levels = self._controller.get_audio_levels()
                    update_data = {**current_status, **audio_levels}
                    self._update_callback(update_data)
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
    
    def refresh_services(self) -> Dict:
        """Force a health poll of the remote services."""
        try:
            return self._controller.refresh_services()
        except Exception as e:
            logger.error(f"Error refreshing services: {e}")
            return {}

    def reconfigure_services(self, settings) -> bool:
        """Apply new EdgeSettings; report the backend error on failure."""
        ok = self._controller.reconfigure_services(settings)
        if self._status_callback:
            if ok:
                self._status_callback('Service settings applied')
            else:
                err = self.last_error() or 'unknown error'
                self._status_callback(f'Settings rejected: {err}')
        return ok

    def last_error(self) -> Optional[str]:
        """Last backend error (settings rejected / start failed)."""
        adapter = getattr(self._controller, '_adapter', None)
        return getattr(adapter, 'last_error', None)

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
    
    def cleanup(self):
        """Clean up resources."""
        self._controller.cleanup()