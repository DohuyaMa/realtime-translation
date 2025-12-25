import numpy as np
from typing import Optional, Callable, List, Dict
from collections import deque
import threading
import time
from loguru import logger

class AudioProcessor:
    """Audio processing pipeline for real-time translation."""
    
    def __init__(
        self,
        sample_rate: int = 16000,
        chunk_size: int = 1024,
        silence_threshold: float = 0.01,
        min_speech_duration: float = 0.5,
        max_speech_duration: float = 10.0
    ):
        """Initialize audio processor.
        
        Args:
            sample_rate: Audio sample rate in Hz
            chunk_size: Size of audio chunks to process
            silence_threshold: Threshold for silence detection
            min_speech_duration: Minimum duration of speech segment in seconds
            max_speech_duration: Maximum duration of speech segment in seconds
        """
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size
        self.silence_threshold = silence_threshold
        self.min_samples = int(min_speech_duration * sample_rate)
        self.max_samples = int(max_speech_duration * sample_rate)
        
        # Audio buffers
        self.buffer = deque(maxlen=self.max_samples)
        self.speech_buffer = []
        
        # State
        self.is_speech_active = False
        self.silence_counter = 0
        self.speech_start_time = 0
        self.lock = threading.Lock()
        
        # Callbacks
        self.on_speech_detected: Optional[Callable[[np.ndarray], None]] = None
        self.on_silence_detected: Optional[Callable[[], None]] = None
        
        logger.info(f"Audio processor initialized: {sample_rate}Hz, {chunk_size} chunk size")

    def set_callbacks(
        self,
        speech_callback: Optional[Callable[[np.ndarray], None]] = None,
        silence_callback: Optional[Callable[[], None]] = None
    ):
        """Set callbacks for speech and silence detection.
        
        Args:
            speech_callback: Called when speech segment is complete
            silence_callback: Called when silence is detected
        """
        self.on_speech_detected = speech_callback
        self.on_silence_detected = silence_callback

    def process_chunk(self, audio_chunk: np.ndarray):
        """Process incoming audio chunk.
        
        Args:
            audio_chunk: Numpy array of audio samples
        """
        with self.lock:
            # Add chunk to buffer
            self.buffer.extend(audio_chunk)
            
            # Calculate RMS energy
            energy = np.sqrt(np.mean(np.square(audio_chunk)))
            
            # Speech detection with improved algorithm
            # Use both energy threshold and some basic spectral features
            is_speech = energy > self.silence_threshold
            
            # For more complex audio (like sine waves), also consider variance
            if not is_speech and len(audio_chunk) > 1:
                variance = np.var(audio_chunk)
                is_speech = variance > (self.silence_threshold * 0.1)  # Lower threshold for variance
            
            if is_speech and not self.is_speech_active:
                # Speech start
                self.is_speech_active = True
                self.speech_start_time = time.time()
                self.speech_buffer = []
                self.silence_counter = 0
                logger.debug("Speech started")
            
            if self.is_speech_active:
                # Add audio to speech buffer
                self.speech_buffer.extend(audio_chunk)
                
                if is_speech:
                    self.silence_counter = 0
                else:
                    self.silence_counter += len(audio_chunk)
                
                # Check if speech segment is complete
                if self._is_speech_complete():
                    self._process_speech_segment()

    def _is_speech_complete(self) -> bool:
        """Check if current speech segment is complete.
        
        Returns:
            True if speech segment should be processed
        """
        # Check for silence
        if self.silence_counter > self.sample_rate * 0.5:  # 0.5 seconds of silence
            return True
            
        # Check maximum duration
        if len(self.speech_buffer) >= self.max_samples:
            return True
            
        return False

    def _process_speech_segment(self):
        """Process completed speech segment."""
        if len(self.speech_buffer) < self.min_samples:
            logger.debug("Speech segment too short, discarding")
            self._reset_state()
            return
            
        try:
            # Convert buffer to numpy array
            speech_data = np.array(self.speech_buffer)
            
            # Notify speech detected
            if self.on_speech_detected:
                self.on_speech_detected(speech_data)
            
            duration = len(speech_data) / self.sample_rate
            logger.debug(f"Processed speech segment: {duration:.2f}s")
            
        except Exception as e:
            logger.error(f"Error processing speech segment: {e}")
        
        finally:
            self._reset_state()

    def _reset_state(self):
        """Reset speech detection state."""
        self.is_speech_active = False
        self.speech_buffer = []
        self.silence_counter = 0
        
        if self.on_silence_detected:
            self.on_silence_detected()

    def get_audio_level(self) -> float:
        """Get current audio level.
        
        Returns:
            RMS energy of recent audio
        """
        with self.lock:
            if len(self.buffer) > 0:
                return float(np.sqrt(np.mean(np.square(list(self.buffer)[-self.chunk_size:]))))
            return 0.0

    def apply_gain(self, audio_data: np.ndarray, gain_db: float) -> np.ndarray:
        """Apply gain to audio data.
        
        Args:
            audio_data: Input audio samples
            gain_db: Gain in decibels
            
        Returns:
            Audio data with gain applied
        """
        gain_linear = 10 ** (gain_db / 20)
        return audio_data * gain_linear

    def get_stats(self) -> Dict[str, float]:
        """Get audio processing statistics.
        
        Returns:
            Dictionary of current statistics
        """
        with self.lock:
            return {
                'audio_level': self.get_audio_level(),
                'buffer_duration': len(self.buffer) / self.sample_rate,
                'is_speech': self.is_speech_active,
                'silence_duration': self.silence_counter / self.sample_rate
            }

    def reset(self):
        """Reset processor state."""
        with self.lock:
            self.buffer.clear()
            self._reset_state()