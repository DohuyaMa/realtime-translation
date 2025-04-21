import pyaudio
import numpy as np
from loguru import logger
from typing import Optional, Callable, Dict, Any
import queue
import threading

class AudioCapture:
    """Audio capture module for real-time translation system."""
    
    def __init__(
        self,
        input_device_index: Optional[int] = None,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_size: int = 1024,
        format_type: int = pyaudio.paFloat32,
        buffer_size_ms: int = 50,  # Buffer size in milliseconds
        resampling_quality: str = 'high'  # low, medium, high
    ):
        """Initialize audio capture with specified parameters.
        
        Args:
            input_device_index: Input device index, None for default
            sample_rate: Audio sample rate (Hz)
            channels: Number of audio channels (1 for mono, 2 for stereo)
            chunk_size: Number of frames per buffer
            format_type: Audio format type from pyaudio
        """
        self.input_device_index = input_device_index
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_size = chunk_size
        self.format_type = format_type
        # Audio configuration
        self.audio = pyaudio.PyAudio()
        self.stream: Optional[pyaudio.Stream] = None
        self.is_running = False
        
        # Calculate optimal buffer sizes
        self.buffer_size = int(sample_rate * buffer_size_ms / 1000)
        self.audio_queue = queue.Queue(maxsize=5)  # Limit queue size
        
        # Callbacks
        self.callback_fn: Optional[Callable[[np.ndarray], None]] = None
        self.device_error_fn: Optional[Callable[[str], None]] = None
        
        # Monitoring
        self.peak_level = 0.0
        self.rms_level = 0.0
        self.monitor_thread: Optional[threading.Thread] = None
        
        # Resampling
        self.resampling_quality = resampling_quality
        self._init_resampling()
        self.callback_fn: Optional[Callable[[np.ndarray], None]] = None

    def get_input_devices(self) -> Dict[int, str]:
        """Get available input devices.
        
        Returns:
            Dictionary of device indices and their names
        """
        devices = {}
        for i in range(self.audio.get_device_count()):
            device_info = self.audio.get_device_info_by_index(i)
            if device_info["maxInputChannels"] > 0:
                devices[i] = device_info["name"]
        return devices

    def set_callback(self, callback_fn: Callable[[np.ndarray], None]):
        """Set callback function for audio processing.
        
        Args:
            callback_fn: Function to process audio chunks
        """
        self.callback_fn = callback_fn

    def audio_callback(self, in_data: bytes, frame_count: int, time_info: Dict[str, Any], status: int):
        """PyAudio callback function.
        
        Args:
            in_data: Input audio data
            frame_count: Number of frames
            time_info: Timing information
            status: Status flag
        """
        if status:
            logger.warning(f"Audio callback status: {status}")
            
        try:
            audio_data = np.frombuffer(in_data, dtype=np.float32)
            
            # Update audio levels
            self._update_levels(audio_data)
            
            # Resample if needed
            if self.sample_rate != self.device_sample_rate:
                audio_data = self._resample_audio(audio_data)
            
            if not self.audio_queue.full():
                self.audio_queue.put(audio_data)
            else:
                logger.warning("Audio queue full, dropping frame")
        except Exception as e:
            logger.error(f"Error processing audio data: {e}")
            if self.device_error_fn:
                self.device_error_fn(str(e))
            else:
                logger.warning("Audio queue full, dropping frame")
        
        if self.callback_fn:
            try:
                self.callback_fn(audio_data)
            except Exception as e:
                logger.error(f"Error in audio callback: {e}")
        
        return (None, pyaudio.paContinue)

    def start(self):
        """Start audio capture."""
        if self.is_running:
            logger.warning("Audio capture already running")
            return

        try:
            self.stream = self.audio.open(
                format=self.format_type,
                channels=self.channels,
                rate=self.sample_rate,
                input=True,
                input_device_index=self.input_device_index,
                frames_per_buffer=self.chunk_size,
                stream_callback=self.audio_callback
            )
            
            self.is_running = True
            logger.info("Audio capture started")
            
            # Start processing thread
            self.process_thread = threading.Thread(target=self._process_audio)
            self.process_thread.daemon = True
            self.process_thread.start()
            
        except Exception as e:
            logger.error(f"Failed to start audio capture: {e}")
            raise

    def _process_audio(self):
        """Process audio data from the queue."""
        while self.is_running:
            try:
                audio_data = self.audio_queue.get(timeout=1.0)
                if self.callback_fn:
                    self.callback_fn(audio_data)
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Error processing audio: {e}")

    def stop(self):
        """Stop audio capture."""
        if not self.is_running:
            return

        self.is_running = False
        
        if self.stream:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception as e:
                logger.error(f"Error stopping audio stream: {e}")
            
        self.audio.terminate()
        logger.info("Audio capture stopped")

    def get_audio_levels(self) -> Dict[str, float]:
        """Get current audio levels.
        
        Returns:
            Dictionary with peak and RMS levels
        """
        return {
            'peak': self.peak_level,
            'rms': self.rms_level
        }

    def _update_levels(self, audio_data: np.ndarray):
        """Update audio level measurements."""
        if len(audio_data) > 0:
            self.peak_level = float(np.max(np.abs(audio_data)))
            self.rms_level = float(np.sqrt(np.mean(np.square(audio_data))))

    def _init_resampling(self):
        """Initialize resampling configuration."""
        try:
            device_info = self.audio.get_device_info_by_index(
                self.input_device_index if self.input_device_index is not None else
                self.audio.get_default_input_device_info()['index']
            )
            self.device_sample_rate = int(device_info['defaultSampleRate'])
            
            if self.device_sample_rate != self.sample_rate:
                logger.info(f"Resampling from {self.device_sample_rate}Hz to {self.sample_rate}Hz")
                
        except Exception as e:
            logger.error(f"Error initializing resampling: {e}")
            self.device_sample_rate = self.sample_rate

    def _resample_audio(self, audio_data: np.ndarray) -> np.ndarray:
        """Resample audio data to target sample rate."""
        try:
            # Implement resampling based on quality setting
            # This is a placeholder - you should implement actual resampling
            return audio_data
        except Exception as e:
            logger.error(f"Resampling error: {e}")
            return audio_data

    def set_device_error_callback(self, callback: Callable[[str], None]):
        """Set callback for device errors.
        
        Args:
            callback: Function to call with error message
        """
        self.device_error_fn = callback

    def __del__(self):
        """Cleanup on deletion."""
        self.stop()