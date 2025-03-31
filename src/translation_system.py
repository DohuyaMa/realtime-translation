from typing import Optional, Dict
import threading
import queue
from loguru import logger

from .audio.capture import AudioCapture
from .audio.routing import AudioRouter
from .audio.processor import AudioProcessor
from .models.whisper_recognition import WhisperRecognizer
from .models.tts_engine import TTSEngine

class TranslationSystem:
    """Main coordinator for the real-time translation system."""
    
    def __init__(
        self,
        source_lang: str = "auto",
        target_lang: str = "en",
        sample_rate: int = 16000,
        use_virtual_audio: bool = True
    ):
        """Initialize translation system.
        
        Args:
            source_lang: Source language code (uk for Ukrainian, pl for Polish, or auto)
            target_lang: Target language code (en for English)
            sample_rate: Audio sample rate
            use_virtual_audio: Whether to use virtual audio devices
        """
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.sample_rate = sample_rate
        
        # Components
        self.audio_router: Optional[AudioRouter] = None
        self.audio_capture: Optional[AudioCapture] = None
        self.audio_processor: Optional[AudioProcessor] = None
        self.recognizer: Optional[WhisperRecognizer] = None
        self.tts_engine: Optional[TTSEngine] = None
        
        # State
        self.is_running = False
        self.translation_enabled = True
        self.status_callback: Optional[callable] = None
        
        # Initialize components
        self._initialize_components(use_virtual_audio)
        
        logger.info(f"Translation system initialized: {source_lang}->{target_lang}")

    def _initialize_components(self, use_virtual_audio: bool):
        """Initialize system components.
        
        Args:
            use_virtual_audio: Whether to set up virtual audio devices
        """
        try:
            # Set up audio routing if requested
            if use_virtual_audio:
                self.audio_router = AudioRouter()
                input_device, output_device = self.audio_router.create_virtual_devices()
                logger.info(f"Virtual audio devices created: {input_device}, {output_device}")
            
            # Initialize audio capture
            self.audio_capture = AudioCapture(
                sample_rate=self.sample_rate,
                channels=1
            )
            
            # Initialize audio processor
            self.audio_processor = AudioProcessor(
                sample_rate=self.sample_rate,
                silence_threshold=0.01,
                min_speech_duration=0.5
            )
            
            # Initialize speech recognition
            self.recognizer = WhisperRecognizer(
                source_lang=self.source_lang,
                target_lang=self.target_lang
            )
            
            # Initialize TTS
            self.tts_engine = TTSEngine(
                sample_rate=self.sample_rate
            )
            
            # Set up callbacks
            self.audio_capture.set_callback(self.audio_processor.process_chunk)
            self.audio_processor.set_callbacks(
                speech_callback=self._handle_speech_segment,
                silence_callback=self._handle_silence
            )
            self.recognizer.set_callback(self._handle_recognition_result)
            self.tts_engine.set_callback(self._handle_synthesized_audio)
            
        except Exception as e:
            logger.error(f"Failed to initialize components: {e}")
            raise

    def _handle_speech_segment(self, audio_data):
        """Handle detected speech segment.
        
        Args:
            audio_data: Audio samples for the speech segment
        """
        if self.translation_enabled and self.recognizer:
            self.recognizer.process_audio(audio_data)
            
        if self.status_callback:
            self.status_callback({
                'status': 'speech_detected',
                'duration': len(audio_data) / self.sample_rate
            })

    def _handle_silence(self):
        """Handle detected silence period."""
        if self.status_callback:
            self.status_callback({
                'status': 'silence_detected'
            })

    def _handle_recognition_result(self, result: Dict):
        """Handle speech recognition result.
        
        Args:
            result: Recognition result dictionary
        """
        if not self.translation_enabled:
            return
            
        text = result.get('text', '').strip()
        if text and self.tts_engine:
            self.tts_engine.synthesize(
                text,
                play_audio=True
            )
            
        if self.status_callback:
            self.status_callback({
                'status': 'recognition_complete',
                'text': text,
                'language': result.get('language'),
                'processing_time': result.get('processing_time')
            })

    def _handle_synthesized_audio(self, audio_data):
        """Handle synthesized audio from TTS.
        
        Args:
            audio_data: Synthesized audio samples
        """
        if self.status_callback:
            self.status_callback({
                'status': 'synthesis_complete',
                'duration': len(audio_data) / self.sample_rate
            })

    def start(self):
        """Start the translation system."""
        if self.is_running:
            logger.warning("Translation system already running")
            return
            
        try:
            if self.audio_capture:
                self.audio_capture.start()
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
            if self.audio_capture:
                self.audio_capture.stop()
                
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
        
        if self.recognizer:
            self.recognizer.set_languages(source_lang, target_lang)
            
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
        
        if self.audio_processor:
            stats.update(self.audio_processor.get_stats())
            
        return stats

    def cleanup(self):
        """Clean up system resources."""
        self.stop()
        
        if self.audio_router:
            self.audio_router.cleanup()
            
        if self.tts_engine:
            self.tts_engine.stop()
            
        if self.recognizer:
            self.recognizer.stop()
            
        logger.info("Translation system cleaned up")

    def __del__(self):
        """Cleanup on deletion."""
        self.cleanup()