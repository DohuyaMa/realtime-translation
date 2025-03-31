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
        format_type: int = pyaudio.paFloat32
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
        
        self.audio = pyaudio.PyAudio()
        self.stream: Optional[pyaudio.Stream] = None
        self.is_running = False
        self.audio_queue = queue.Queue()
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
            
        audio_data = np.frombuffer(in_data, dtype=np.float32)
        self.audio_queue.put(audio_data)
        
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

    def __del__(self):
        """Cleanup on deletion."""
        self.stop()