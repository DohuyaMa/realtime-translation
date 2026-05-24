"""Text-to-speech service using UNIX sockets for IPC."""

from loguru import logger
from typing import Dict, Any, Optional
import threading
import time
import base64
import sys

from ..common.ipc import IPCServer
from ..status_logger import StatusManager
from ..models.tts_engine import TTSEngine
from ..core.runtime import get_runtime_config

_log_timing = time.monotonic


class TTSService:
    """Text-to-speech service for the real-time translation system."""
    
    def __init__(
        self,
        socket_path: str,
        sample_rate: int = 24000,
        voice: Optional[str] = None,
        speed: float = 1.0,
    ):
        """Initialize TTS service."""
        self.socket_path = socket_path
        self.sample_rate = sample_rate
        self._voice = voice
        self._speed = speed

        # Initialize TTS engine
        self.tts_engine = TTSEngine(
            sample_rate=sample_rate,
            voice=voice,
        )

        # IPC setup
        self.ipc_server = IPCServer(socket_path)
        self.ipc_server.register_handler('synthesize_text', self._handle_synthesize_text)
        self.ipc_server.register_handler('get_status', self._handle_get_status)
        self.ipc_server.register_handler('play_audio', self._handle_play_audio)
        self.ipc_server.register_handler('set_voice', self._handle_set_voice)
        
        # State
        # State
        self.is_running = False
        self.processing_lock = threading.Lock()
        
        # Status manager
        self.status = StatusManager(component_name="tts")
        
        logger.info("TTS service initialized")
        self.status.log_info("TTS service initialized")
        self.status.set_status("Initializing TTS engine...")
    def start(self):
        """Start the TTS service."""
        self.ipc_server.start()
        self.is_running = True
        logger.info("TTS service started")
        self.status.set_status("Ready for TTS synthesis...")
        self.status.log_info("TTS service started")
    
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
                
                self.status.set_status("Synthesizing audio...")
                self.status.log_debug(f"TTS request: {len(text)} chars: {text[:80]}{'...' if len(text) > 80 else ''}")
                
                # Synthesize speech synchronously so we can return audio bytes
                t0 = _log_timing()
                audio_data = self.tts_engine.synthesize_sync(text, speed=self._speed)
                tts_time = _log_timing() - t0
                
                # Convert audio data to base64 for transmission
                if audio_data is not None:
                    audio_bytes = audio_data.astype(audio_data.dtype).tobytes()
                    audio_b64 = base64.b64encode(audio_bytes).decode('utf-8')
                    audio_duration = len(audio_data) / self.sample_rate if len(audio_data) > 0 else 0
                    
                    self.status.log_info(
                        f"TTS generated in {tts_time*1000:.0f}ms: "
                        f"{len(text)} chars → {audio_duration:.1f}s audio "
                        f"({len(audio_bytes)}B @ {self.sample_rate}Hz)"
                    )
                    
                    return {
                        "status": "success",
                        "data": {
                            "audio_data": audio_b64,
                            "format": str(audio_data.dtype),
                            "sample_rate": self.sample_rate,
                            "duration": audio_duration,
                            "timing_ms": round(tts_time * 1000, 1),
                        }
                    }
                else:
                    self.status.log_error(f"TTS returned None for text ({len(text)} chars): {text[:100]}")
                    return {"status": "error", "message": "Failed to synthesize audio"}
                
            except Exception as e:
                self.status.log_exception(f"TTS synthesis error: {e}")
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
                duration_sec = len(audio_bytes) / (2 * self.sample_rate)  # rough: int16 = 2 bytes
                
                self.status.log_debug(f"Playback requested: {len(audio_bytes)}B ≈ {duration_sec:.1f}s")
                
                return {
                    "status": "success",
                    "message": "Audio playback initiated",
                    "data": {"duration_sec": duration_sec}
                }
                
            except Exception as e:
                self.status.log_exception(f"Error playing audio: {e}")
                return {"status": "error", "message": str(e)}
    
    def _handle_set_voice(self, message: Dict) -> Dict[str, Any]:
        """Handle live voice/speed change request from IPC."""
        try:
            voice = message.get('data', {}).get('voice', self._voice)
            speed = message.get('data', {}).get('speed', self._speed)
            self._voice = voice
            self._speed = speed
            self.tts_engine.set_voice(voice)
            self.status.log_info(f"Voice changed to {voice}, speed={speed}")
            return {"status": "success", "data": {"voice": voice, "speed": speed}}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _handle_get_status(self, message: Dict) -> Dict[str, Any]:
        """Handle get status request from IPC."""
        return {
            "status": "success",
            "data": {
                "running": self.is_running,
                "sample_rate": self.sample_rate,
                "voice": self._voice,
                "speed": self._speed,
            }
        }


def main():
    """Main entry point for the TTS service."""
    import argparse
    import os
    import signal
    
    parser = argparse.ArgumentParser(description="Text-to-Speech Service")
    parser.add_argument("--socket-path", default=get_runtime_config().get_tts_socket_path(),
                       help="Path to UNIX socket for IPC")
    parser.add_argument("--sample-rate", type=int, default=24000,
                       help="Audio sample rate")
    parser.add_argument("--voice", default="af_heart",
                       help="Kokoro voice name (e.g. af_heart, af_bella, am_adam)")
    parser.add_argument("--speed", type=float, default=1.0,
                       help="Speech speed multiplier (0.5–2.0)")

    args = parser.parse_args()

    # Create temporary directory if needed
    socket_dir = os.path.dirname(args.socket_path)
    os.makedirs(socket_dir, exist_ok=True)

    service = TTSService(
        socket_path=args.socket_path,
        sample_rate=args.sample_rate,
        voice=args.voice,
        speed=args.speed,
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