"""Main coordinator for the real-time translation system using modular architecture."""

from typing import Optional, Dict
import threading
import queue
from loguru import logger

from .audio.routing import AudioRouter
from .common.ipc import IPCClient


class TranslationSystem:
    """Main coordinator for the real-time translation system."""
    
    def __init__(
        self,
        source_lang: str = "auto",
        target_lang: str = "en",
        sample_rate: int = 16000
    ):
        """Initialize translation system.
        
        Args:
            source_lang: Source language code (uk for Ukrainian, pl for Polish, or auto)
            target_lang: Target language code (en for English)
            sample_rate: Audio sample rate
        """
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.sample_rate = sample_rate
        
        # Components
        self.audio_router: Optional[AudioRouter] = None
        
        # IPC clients for modular services
        self.capture_client: Optional[IPCClient] = None
        self.whisper_client: Optional[IPCClient] = None
        self.translate_client: Optional[IPCClient] = None
        self.tts_client: Optional[IPCClient] = None
        self.playback_client: Optional[IPCClient] = None
        
        # State
        self.is_running = False
        self.translation_enabled = True
        self.status_callback: Optional[callable] = None
        
        # Initialize components
        self._initialize_components()
        
        logger.info(f"Translation system initialized: {source_lang}->{target_lang}")

    def _initialize_components(self):
        """Initialize system components."""
        try:
            # Set up audio routing with fixed device names
            self.audio_router = AudioRouter()
            input_device, output_device = self.audio_router.get_virtual_devices()
            logger.info(f"Using virtual audio devices: {input_device}, {output_device}")
            
            # Initialize IPC clients for modular services
            self.capture_client = IPCClient("/tmp/rt-capture.sock")
            self.whisper_client = IPCClient("/tmp/rt-whisper.sock")
            self.translate_client = IPCClient("/tmp/rt-translate.sock")
            self.tts_client = IPCClient("/tmp/rt-tts.sock")
            self.playback_client = IPCClient("/tmp/rt-playback.sock")
            
            # Connect to services
            try:
                self.capture_client.connect()
                self.whisper_client.connect()
                self.translate_client.connect()
                self.tts_client.connect()
                self.playback_client.connect()
                logger.info("Connected to all modular services")
            except Exception as e:
                logger.error(f"Failed to connect to services: {e}")
                # We'll continue but services need to be started separately
                
        except Exception as e:
            logger.error(f"Failed to initialize components: {e}")
            raise

    def process_audio_chunk(self, audio_data):
        """Process an audio chunk through the translation pipeline.
        
        Args:
            audio_data: Audio samples to process
        """
        if not self.translation_enabled:
            return
            
        try:
            # Send audio to whisper service for recognition
            if self.whisper_client:
                import base64
                audio_bytes = audio_data.astype(audio_data.dtype).tobytes()
                audio_b64 = base64.b64encode(audio_bytes).decode('utf-8')
                
                result = self.whisper_client.send_message('process_audio', {
                    'data': audio_b64,
                    'format': str(audio_data.dtype),
                    'sample_rate': self.sample_rate
                })
                
                if result and result.get('status') == 'success':
                    text = result['data']['text']
                    logger.info(f"Recognized: {text}")
                    
                    # Translate the text
                    if self.translate_client:
                        translation_result = self.translate_client.send_message('translate_text', {
                            'text': text
                        })
                        
                        if translation_result and translation_result.get('status') == 'success':
                            translated_text = translation_result['data']['translated_text']
                            logger.info(f"Translated: {translated_text}")
                            
                            # Synthesize the translated text
                            if self.tts_client:
                                synthesis_result = self.tts_client.send_message('synthesize_text', {
                                    'text': translated_text
                                })
                                
                                if synthesis_result and synthesis_result.get('status') == 'success':
                                    audio_data_b64 = synthesis_result['data']['audio_data']
                                    
                                    # Play the synthesized audio
                                    if self.playback_client:
                                        self.playback_client.send_message('play_audio', {
                                            'audio_data': audio_data_b64
                                        })
                                        
                                        if self.status_callback:
                                            self.status_callback({
                                                'status': 'translation_complete',
                                                'original_text': text,
                                                'translated_text': translated_text,
                                                'duration': synthesis_result['data'].get('duration', 0)
                                            })
        except Exception as e:
            logger.error(f"Error processing audio chunk: {e}")

    def _handle_silence(self):
        """Handle detected silence period."""
        if self.status_callback:
            self.status_callback({
                'status': 'silence_detected'
            })

    def start(self):
        """Start the translation system."""
        if self.is_running:
            logger.warning("Translation system already running")
            return
             
        try:
            # Start capture if client is available
            if self.capture_client:
                self.capture_client.send_message('start_capture', {})
            self.is_running = True
            logger.info("Translation system started")
             
        except Exception as e:
            logger.error(f"Failed to start translation system: {e}")
            raise

    def stop(self):
        """Stop the translation system."""
        if not self.is_running:
            return
             
        try:
            # Stop capture if client is available
            if self.capture_client:
                self.capture_client.send_message('stop_capture', {})
                 
            self.is_running = False
            logger.info("Translation system stopped")
             
        except Exception as e:
            logger.error(f"Error stopping translation system: {e}")

    def set_languages(self, source_lang: str, target_lang: str = "en"):
        """Set source and target languages.
        
        Args:
            source_lang: Source language code
            target_lang: Target language code
        """
        self.source_lang = source_lang
        self.target_lang = target_lang
         
        # Update whisper service languages
        if self.whisper_client:
            self.whisper_client.send_message('set_languages', {
                'data': {
                    'source_lang': source_lang,
                    'target_lang': target_lang
                }
            })
             
        # Update translation service languages
        if self.translate_client:
            self.translate_client.send_message('set_languages', {
                'data': {
                    'source_lang': source_lang,
                    'target_lang': target_lang
                }
            })
             
        logger.info(f"Languages updated: {source_lang}->{target_lang}")

    def set_status_callback(self, callback: callable):
        """Set callback for system status updates.
        
        Args:
            callback: Function to call with status updates
        """
        self.status_callback = callback

    def toggle_translation(self, enabled: bool):
        """Enable or disable translation.
        
        Args:
            enabled: Whether translation should be enabled
        """
        self.translation_enabled = enabled
        logger.info(f"Translation {'enabled' if enabled else 'disabled'}")

    def get_audio_devices(self) -> Dict[str, Dict]:
        """Get available audio devices.
        
        Returns:
            Dictionary of available input and output devices
        """
        if self.audio_router:
            return self.audio_router.list_devices()
        return {'inputs': {}, 'outputs': {}}

    def get_stats(self) -> Dict:
        """Get system statistics.
        
        Returns:
            Dictionary of current system statistics
        """
        stats = {
            'running': self.is_running,
            'translation_enabled': self.translation_enabled,
            'source_language': self.source_lang,
            'target_language': self.target_lang
        }
        
        if self.capture_client:
            try:
                status = self.capture_client.send_message('get_status', {})
                if status and status.get('status') == 'success':
                    stats.update(status.get('data', {}))
            except:
                pass
             
        return stats

    def cleanup(self):
        """Clean up system resources."""
        self.stop()
         
        if self.audio_router:
            self.audio_router.cleanup()
             
        # Disconnect IPC clients
        for client in [self.capture_client, self.whisper_client,
                      self.translate_client, self.tts_client, self.playback_client]:
            if client:
                try:
                    client.disconnect()
                except:
                    pass
             
        logger.info("Translation system cleaned up")

    def __del__(self):
        """Cleanup on deletion."""
        self.cleanup()