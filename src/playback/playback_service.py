"""Audio playback service using UNIX sockets for IPC."""

import pyaudio
import numpy as np
from loguru import logger
from typing import Dict, Any, Optional
import threading
import time
import base64
import sys

from ..common.ipc import IPCServer


class PlaybackService:
    """Audio playback service for the real-time translation system."""
    
    def __init__(
        self,
        socket_path: str,
        output_device_index: Optional[int] = None,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_size: int = 1024
    ):
        """Initialize playback service.
        
        Args:
            socket_path: Path to the UNIX socket for IPC
            output_device_index: Output device index, None for default
            sample_rate: Audio sample rate (Hz)
            channels: Number of audio channels (1 for mono, 2 for stereo)
            chunk_size: Number of frames per buffer
        """
        self.output_device_index = output_device_index
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_size = chunk_size
        
        # Audio configuration
        self.audio = pyaudio.PyAudio()
        self.stream: Optional[pyaudio.Stream] = None
        self.is_running = False
        
        # IPC setup
        self.ipc_server = IPCServer(socket_path)
        self.ipc_server.register_handler('play_audio', self._handle_play_audio)
        self.ipc_server.register_handler('get_status', self._handle_get_status)
        self.ipc_server.register_handler('set_device', self._handle_set_device)
        
        # Audio queue for playback
        self.audio_queue = []
        self.playback_lock = threading.Lock()
        
        logger.info(f"Playback service initialized: {sample_rate}Hz, {channels} channels")
    
    def start(self):
        """Start the playback service."""
        self.ipc_server.start()
        
        # Open audio stream for output to rt_virtual_output
        try:
            self.stream = self.audio.open(
                format=pyaudio.paFloat32,
                channels=self.channels,
                rate=self.sample_rate,
                output=True,
                output_device_index=self.output_device_index,
                frames_per_buffer=self.chunk_size
            )
            logger.info("Playback stream opened")
        except Exception as e:
            logger.error(f"Could not open playback stream: {e}")
            # Continue anyway as this might be handled differently in the actual setup
        
        self.is_running = True
        logger.info("Playback service started")
    
    def stop(self):
        """Stop the playback service."""
        self.is_running = False
        
        if self.stream:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception as e:
                logger.error(f"Error stopping playback stream: {e}")
        
        self.audio.terminate()
        self.ipc_server.stop()
        logger.info("Playback service stopped")
    
    def _handle_play_audio(self, message: Dict) -> Dict[str, Any]:
        """Handle audio playback request from IPC."""
        with self.playback_lock:
            try:
                audio_data_b64 = message.get('data', {}).get('audio_data')
                if not audio_data_b64:
                    return {"status": "error", "message": "No audio data provided"}
                
                # Decode base64 audio data
                audio_bytes = base64.b64decode(audio_data_b64)
                
                # Convert to numpy array (assuming float32)
                audio_array = np.frombuffer(audio_bytes, dtype=np.float32)
                
                # Play audio through the stream
                if self.stream:
                    try:
                        self.stream.write(audio_array.tobytes())
                        logger.debug(f"Played audio: {len(audio_array)} samples")
                    except Exception as e:
                        logger.error(f"Error writing audio to stream: {e}")
                        return {"status": "error", "message": str(e)}
                
                return {
                    "status": "success",
                    "message": f"Played {len(audio_array)} samples"
                }
                
            except Exception as e:
                logger.error(f"Error playing audio: {e}")
                return {"status": "error", "message": str(e)}
    
    def _handle_get_status(self, message: Dict) -> Dict[str, Any]:
        """Handle get status request from IPC."""
        return {
            "status": "success",
            "data": {
                "running": self.is_running,
                "sample_rate": self.sample_rate,
                "channels": self.channels
            }
        }
    
    def _handle_set_device(self, message: Dict) -> Dict[str, Any]:
        """Handle set output device request from IPC."""
        try:
            device_index = message.get('data', {}).get('device_index')
            if device_index is not None:
                self.output_device_index = device_index
                return {
                    "status": "success",
                    "message": f"Output device set to {device_index}"
                }
            else:
                return {
                    "status": "error",
                    "message": "No device index provided"
                }
        except Exception as e:
            logger.error(f"Error setting output device: {e}")
            return {"status": "error", "message": str(e)}


def main():
    """Main entry point for the playback service."""
    import argparse
    import os
    import signal
    
    parser = argparse.ArgumentParser(description="Audio Playback Service")
    parser.add_argument("--socket-path", default="/tmp/rt-playback.sock", 
                       help="Path to UNIX socket for IPC")
    parser.add_argument("--sample-rate", type=int, default=16000, 
                       help="Audio sample rate")
    parser.add_argument("--channels", type=int, default=1, 
                       help="Number of audio channels")
    
    args = parser.parse_args()
    
    # Create temporary directory if needed
    socket_dir = os.path.dirname(args.socket_path)
    os.makedirs(socket_dir, exist_ok=True)
    
    service = PlaybackService(
        socket_path=args.socket_path,
        sample_rate=args.sample_rate,
        channels=args.channels
    )
    
    def signal_handler(signum, frame):
        logger.info("Received shutdown signal")
        service.stop()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    service.start()
    
    try:
        # Keep the service running
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        service.stop()


if __name__ == "__main__":
    main()