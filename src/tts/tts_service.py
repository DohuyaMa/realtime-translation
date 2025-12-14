"""Text-to-speech service using UNIX sockets for IPC."""

from loguru import logger
from typing import Dict, Any, Optional
import threading
import time
import base64
import sys

from ..common.ipc import IPCServer
from ..models.tts_engine import TTSEngine


class TTSService:
    """Text-to-speech service for the real-time translation system."""
    
    def __init__(
        self,
        socket_path: str,
        sample_rate: int = 16000
    ):
        """Initialize TTS service.
        
        Args:
            socket_path: Path to the UNIX socket for IPC
            sample_rate: Audio sample rate
        """
        self.socket_path = socket_path
        self.sample_rate = sample_rate
        
        # Initialize TTS engine
        self.tts_engine = TTSEngine(
            sample_rate=sample_rate
        )
        
        # IPC setup
        self.ipc_server = IPCServer(socket_path)
        self.ipc_server.register_handler('synthesize_text', self._handle_synthesize_text)
        self.ipc_server.register_handler('get_status', self._handle_get_status)
        self.ipc_server.register_handler('play_audio', self._handle_play_audio)
        
        # State
        self.is_running = False
        self.processing_lock = threading.Lock()
        
        logger.info("TTS service initialized")
    
    def start(self):
        """Start the TTS service."""
        self.ipc_server.start()
        self.is_running = True
        logger.info("TTS service started")
    
    def stop(self):
        """Stop the TTS service."""
        self.is_running = False
        self.ipc_server.stop()
        if self.tts_engine:
            self.tts_engine.stop()
        logger.info("TTS service stopped")
    
    def _handle_synthesize_text(self, message: Dict) -> Dict[str, Any]:
        """Handle text synthesis request from IPC."""
        with self.processing_lock:
            try:
                text = message.get('data', {}).get('text', '')
                if not text:
                    return {"status": "error", "message": "No text provided"}
                
                # Synthesize speech
                audio_data = self.tts_engine.synthesize(text, play_audio=False)
                
                # Convert audio data to base64 for transmission
                if audio_data is not None:
                    audio_bytes = audio_data.astype(audio_data.dtype).tobytes()
                    audio_b64 = base64.b64encode(audio_bytes).decode('utf-8')
                    
                    return {
                        "status": "success",
                        "data": {
                            "audio_data": audio_b64,
                            "format": str(audio_data.dtype),
                            "sample_rate": self.sample_rate,
                            "duration": len(audio_data) / self.sample_rate if len(audio_data) > 0 else 0
                        }
                    }
                else:
                    return {"status": "error", "message": "Failed to synthesize audio"}
                
            except Exception as e:
                logger.error(f"Error synthesizing text: {e}")
                return {"status": "error", "message": str(e)}
    
    def _handle_play_audio(self, message: Dict) -> Dict[str, Any]:
        """Handle audio playback request from IPC."""
        with self.processing_lock:
            try:
                audio_data_b64 = message.get('data', {}).get('audio_data')
                if not audio_data_b64:
                    return {"status": "error", "message": "No audio data provided"}
                
                # Decode base64 audio data
                audio_bytes = base64.b64decode(audio_data_b64)
                
                # Play audio (this would typically involve sending to playback service)
                # For now, we'll just return success as the actual implementation
                # would depend on the specific pipeline setup
                logger.debug(f"Audio playback requested: {len(audio_bytes)} bytes")
                
                return {
                    "status": "success",
                    "message": "Audio playback initiated"
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
                "sample_rate": self.sample_rate
            }
        }


def main():
    """Main entry point for the TTS service."""
    import argparse
    import os
    import signal
    
    parser = argparse.ArgumentParser(description="Text-to-Speech Service")
    parser.add_argument("--socket-path", default="/tmp/rt-tts.sock", 
                       help="Path to UNIX socket for IPC")
    parser.add_argument("--sample-rate", type=int, default=16000, 
                       help="Audio sample rate")
    
    args = parser.parse_args()
    
    # Create temporary directory if needed
    socket_dir = os.path.dirname(args.socket_path)
    os.makedirs(socket_dir, exist_ok=True)
    
    service = TTSService(
        socket_path=args.socket_path,
        sample_rate=args.sample_rate
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