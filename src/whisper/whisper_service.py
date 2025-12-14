"""Whisper speech recognition service using UNIX sockets for IPC."""

import numpy as np
from loguru import logger
from typing import Dict, Any, Optional
import threading
import time
import base64
import sys

from ..common.ipc import IPCServer
from ..models.whisper_recognition import WhisperRecognizer


class WhisperService:
    """Whisper speech recognition service for the real-time translation system."""
    
    def __init__(
        self,
        socket_path: str,
        source_lang: str = "auto",
        target_lang: str = "en",
        model_size: str = "medium"
    ):
        """Initialize Whisper service.
        
        Args:
            socket_path: Path to the UNIX socket for IPC
            source_lang: Source language code
            target_lang: Target language code
            model_size: Whisper model size
        """
        self.socket_path = socket_path
        self.source_lang = source_lang
        self.target_lang = target_lang
        
        # Initialize Whisper recognizer
        self.recognizer = WhisperRecognizer(
            source_lang=source_lang,
            target_lang=target_lang,
            model_size=model_size
        )
        
        # IPC setup
        self.ipc_server = IPCServer(socket_path)
        self.ipc_server.register_handler('process_audio', self._handle_process_audio)
        self.ipc_server.register_handler('get_status', self._handle_get_status)
        self.ipc_server.register_handler('set_languages', self._handle_set_languages)
        
        # State
        self.is_running = False
        self.processing_lock = threading.Lock()
        
        logger.info(f"Whisper service initialized: {source_lang}->{target_lang}")
    
    def start(self):
        """Start the Whisper service."""
        self.ipc_server.start()
        self.is_running = True
        logger.info("Whisper service started")
    
    def stop(self):
        """Stop the Whisper service."""
        self.is_running = False
        self.ipc_server.stop()
        if self.recognizer:
            self.recognizer.stop()
        logger.info("Whisper service stopped")
    
    def _handle_process_audio(self, message: Dict) -> Dict[str, Any]:
        """Handle audio processing request from IPC."""
        with self.processing_lock:
            try:
                # Get audio data from message
                audio_data_b64 = message.get('data', {}).get('data')
                if not audio_data_b64:
                    return {"status": "error", "message": "No audio data provided"}
                
                # Decode base64 audio data
                audio_bytes = base64.b64decode(audio_data_b64)
                
                # Convert to numpy array (assuming float32)
                audio_array = np.frombuffer(audio_bytes, dtype=np.float32)
                
                # Process with Whisper
                result = self.recognizer.process_audio(audio_array)
                
                return {
                    "status": "success",
                    "data": {
                        "text": result.get('text', ''),
                        "language": result.get('language', self.source_lang),
                        "processing_time": result.get('processing_time', 0)
                    }
                }
                
            except Exception as e:
                logger.error(f"Error processing audio: {e}")
                return {"status": "error", "message": str(e)}
    
    def _handle_get_status(self, message: Dict) -> Dict[str, Any]:
        """Handle get status request from IPC."""
        return {
            "status": "success",
            "data": {
                "running": self.is_running,
                "source_language": self.source_lang,
                "target_language": self.target_lang
            }
        }
    
    def _handle_set_languages(self, message: Dict) -> Dict[str, Any]:
        """Handle set languages request from IPC."""
        try:
            source_lang = message.get('data', {}).get('source_lang', self.source_lang)
            target_lang = message.get('data', {}).get('target_lang', self.target_lang)
            
            self.source_lang = source_lang
            self.target_lang = target_lang
            
            # Update recognizer languages
            self.recognizer.set_languages(source_lang, target_lang)
            
            return {
                "status": "success",
                "message": f"Languages updated: {source_lang}->{target_lang}"
            }
            
        except Exception as e:
            logger.error(f"Error setting languages: {e}")
            return {"status": "error", "message": str(e)}


def main():
    """Main entry point for the Whisper service."""
    import argparse
    import os
    import signal
    
    parser = argparse.ArgumentParser(description="Whisper Speech Recognition Service")
    parser.add_argument("--socket-path", default="/tmp/rt-whisper.sock", 
                       help="Path to UNIX socket for IPC")
    parser.add_argument("--source-lang", default="auto", 
                       help="Source language code")
    parser.add_argument("--target-lang", default="en", 
                       help="Target language code")
    parser.add_argument("--model-size", default="medium", 
                       help="Whisper model size")
    
    args = parser.parse_args()
    
    # Create temporary directory if needed
    socket_dir = os.path.dirname(args.socket_path)
    os.makedirs(socket_dir, exist_ok=True)
    
    service = WhisperService(
        socket_path=args.socket_path,
        source_lang=args.source_lang,
        target_lang=args.target_lang,
        model_size=args.model_size
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