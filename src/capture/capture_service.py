"""Audio capture service using UNIX sockets for IPC."""

import pyaudio
import numpy as np
from loguru import logger
from typing import Optional, Dict, Any
import queue
import threading
import time
import subprocess
import sys
import base64

from ..common.ipc import IPCServer
from ..status_logger import StatusManager
from ..core.runtime import get_runtime_config


class AudioCaptureService:
    """Audio capture service for the real-time translation system."""
    
    def __init__(
        self,
        socket_path: str,
        input_device_index: Optional[int] = None,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_size: int = 1024,
    ):
        """Initialize audio capture service.
        
        Args:
            socket_path: Path to the UNIX socket for IPC
            input_device_index: Input device index, None for default
            sample_rate: Audio sample rate (Hz)
            channels: Number of audio channels (1 for mono, 2 for stereo)
            chunk_size: Number of frames per buffer
        """
        self.input_device_index = input_device_index
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_size = chunk_size
        
        # Audio configuration
        self.audio = pyaudio.PyAudio()
        self.stream: Optional[pyaudio.Stream] = None
        self.is_running = False
        
        # IPC setup
        self.ipc_server = IPCServer(socket_path)
        self.ipc_server.register_handler('start_capture', self._handle_start_capture)
        self.ipc_server.register_handler('stop_capture', self._handle_stop_capture)
        self.ipc_server.register_handler('get_status', self._handle_get_status)
        
        # Audio processing
        self.audio_queue = queue.Queue(maxsize=5)
        self.process_thread: Optional[threading.Thread] = None
        
        # Status manager
        self.status = StatusManager(component_name="capture")
        
        # Monitoring
        self.peak_level = 0.0
        self.rms_level = 0.0
        
        # Diagnostics counters
        self._frames_captured = 0
        self._frames_dropped = 0
        self._callback_errors = 0
        
        logger.info(f"Audio capture service initialized: {sample_rate}Hz, {channels} channels")
        self.status.log_info(f"Audio capture service initialized: {sample_rate}Hz, {channels} channels")
        self.status.set_status("Initializing capture device...")
    
    def ensure_pipewire_nodes(self):
        """Ensure that the required PipeWire nodes exist."""
        try:
            # List all sinks and sources for diagnostics
            sinks_result = subprocess.check_output(
                ["pactl", "list", "sinks", "short"],
                text=True
            )
            sources_result = subprocess.check_output(
                ["pactl", "list", "sources", "short"],
                text=True
            )
            logger.info(f"PipeWire sinks available:\n{sinks_result.strip()}")
            logger.info(f"PipeWire sources available:\n{sources_result.strip()}")
            
            if "rt_virtual_output.monitor" not in sources_result:
                self.status.log_error(
                    f"Virtual PipeWire source 'rt_virtual_output.monitor' not found. "
                    f"Available sources: {[l.split()[-1] for l in sources_result.strip().split(chr(10)) if l]}"
                )
                sys.exit("Virtual PipeWire source (monitor) not found. Please set up PipeWire configuration first.")
                
            if "rt_virtual_input" not in sinks_result:
                self.status.log_error(
                    f"Virtual PipeWire sink 'rt_virtual_input' not found. "
                    f"Available sinks: {[l.split()[-1] for l in sinks_result.strip().split(chr(10)) if l]}"
                )
                sys.exit("Virtual PipeWire input sink not found. Please set up PipeWire configuration first.")
                
            self.status.log_info("PipeWire nodes verified successfully")
            
        except subprocess.CalledProcessError as e:
            self.status.log_exception(f"Failed to check PipeWire nodes (exit {e.returncode}): {e.output}")
            sys.exit(f"Failed to check PipeWire nodes: {e}")
        except FileNotFoundError:
            self.status.log_error("pactl command not found. Please ensure PipeWire is installed.")
            sys.exit("pactl command not found. Please ensure PipeWire is installed.")
    
    def start(self):
        """Start the audio capture service."""
        # Verify PipeWire nodes exist
        self.ensure_pipewire_nodes()
        
        # Start IPC server
        self.ipc_server.start()
        
        logger.info("Audio capture service started")
        self.status.set_status("Ready to capture audio...")
        self.status.log_info("Audio capture service started")
    
    def stop(self):
        """Stop the audio capture service."""
        self.is_running = False
        if self.stream:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception as e:
                logger.error(f"Error stopping audio stream: {e}")
        self.audio.terminate()
        self.ipc_server.stop()
        logger.info("Audio capture service stopped")
    
    def audio_callback(self, in_data: bytes, frame_count: int, time_info: Dict[str, Any], status: int):
        """PyAudio callback function."""
        if status:
            self._callback_errors += 1
            self.status.log_warning(
                f"Audio callback status flag: {status} (callback_errors={self._callback_errors})"
            )
            
        try:
            audio_data = np.frombuffer(in_data, dtype=np.float32)
            
            # Update audio levels
            self._update_levels(audio_data)
            self._frames_captured += 1
            
            if not self.audio_queue.full():
                self.audio_queue.put(audio_data)
            else:
                self._frames_dropped += 1
                if self._frames_dropped <= 5 or self._frames_dropped % 50 == 0:
                    self.status.log_warning(
                        f"Audio queue full, dropping frame "
                        f"(dropped={self._frames_dropped}, captured={self._frames_captured})"
                    )
                
        except Exception as e:
            self._callback_errors += 1
            self.status.log_exception(
                f"Error processing audio frame (count={self._frames_captured}): {e}"
            )
        
        return (None, pyaudio.paContinue)
    
    def _process_audio(self):
        """Process audio data from the queue and send via IPC."""
        while self.is_running:
            try:
                audio_data = self.audio_queue.get(timeout=1.0)
                
                # Convert audio data to bytes
                audio_bytes = audio_data.astype(np.float32).tobytes()
                
                # Encode as base64 for JSON transmission
                audio_b64 = base64.b64encode(audio_bytes).decode('utf-8')
                
                # Send audio data via IPC
                try:
                    # This would send to the next service in the pipeline
                    # For now, we just log it as the actual implementation
                    # would depend on the specific pipeline setup
                    logger.debug(f"Audio chunk processed: {len(audio_data)} samples")
                except Exception as e:
                    logger.error(f"Error sending audio via IPC: {e}")
                    
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Error processing audio: {e}")
    
    def _handle_start_capture(self, message: Dict) -> Dict[str, Any]:
        """Handle start capture request from IPC."""
        if self.is_running:
            return {"status": "error", "message": "Capture already running"}
        
        try:
            # Log device information for debugging
            device_count = self.audio.get_device_count()
            try:
                device_info = self.audio.get_device_info_by_index(self.input_device_index) if self.input_device_index is not None else None
                if device_info:
                    self.status.log_info(
                        f"Opening input device [{self.input_device_index}]: "
                        f"'{device_info.get('name', '?')}' "
                        f"sr={int(device_info.get('defaultSampleRate', 0))}Hz "
                        f"max_ch={device_info.get('maxInputChannels', 0)}"
                    )
                else:
                    self.status.log_info(
                        f"Opening default input device "
                        f"(available devices: {device_count})"
                    )
            except Exception as e:
                self.status.log_debug(f"Could not query device info: {e}")
            
            self.stream = self.audio.open(
                format=pyaudio.paFloat32,
                channels=self.channels,
                rate=self.sample_rate,
                input=True,
                input_device_index=self.input_device_index,
                frames_per_buffer=self.chunk_size,
                stream_callback=self.audio_callback
            )
            
            self.is_running = True
            self._frames_captured = 0
            self._frames_dropped = 0
            self._callback_errors = 0
            self.status.log_info(
                f"Audio capture started: {self.sample_rate}Hz "
                f"{self.channels}ch chunk={self.chunk_size} "
                f"device={self.input_device_index or 'default'}"
            )
            self.status.set_status("Capturing audio...")
            
            # Start processing thread
            self.process_thread = threading.Thread(target=self._process_audio)
            self.process_thread.daemon = True
            self.process_thread.start()
            
            return {"status": "success", "message": "Capture started"}
            
        except Exception as e:
            logger.error(f"Failed to start audio capture: {e}")
            self.status.log_error(f"Failed to start audio capture: {e}")
            return {"status": "error", "message": str(e)}
    
    def _handle_stop_capture(self, message: Dict) -> Dict[str, Any]:
        """Handle stop capture request from IPC."""
        if not self.is_running:
            return {"status": "error", "message": "Capture not running"}
        
        self.is_running = False
        
        if self.stream:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception as e:
                self.status.log_exception(f"Error stopping audio stream: {e}")
        
        self.status.log_info(
            f"Audio capture stopped: "
            f"frames={self._frames_captured} dropped={self._frames_dropped} "
            f"cb_errors={self._callback_errors}"
        )
        self.status.set_status("Capture stopped")
        return {"status": "success", "message": "Capture stopped"}
    
    def _handle_get_status(self, message: Dict) -> Dict[str, Any]:
        """Handle get status request from IPC."""
        return {
            "status": "success",
            "data": {
                "running": self.is_running,
                "peak_level": self.peak_level,
                "rms_level": self.rms_level,
                "sample_rate": self.sample_rate,
                "channels": self.channels,
                "frames_captured": self._frames_captured,
                "frames_dropped": self._frames_dropped,
                "callback_errors": self._callback_errors,
            }
        }
    
    def _update_levels(self, audio_data: np.ndarray):
        """Update audio level measurements."""
        if len(audio_data) > 0:
            self.peak_level = float(np.max(np.abs(audio_data)))
            self.rms_level = float(np.sqrt(np.mean(np.square(audio_data))))


def main():
    """Main entry point for the capture service."""
    import argparse
    import os
    import signal
    
    parser = argparse.ArgumentParser(description="Audio Capture Service")
    parser.add_argument("--socket-path", default=get_runtime_config().get_capture_socket_path(),
                       help="Path to UNIX socket for IPC")
    parser.add_argument("--sample-rate", type=int, default=16000, 
                       help="Audio sample rate")
    parser.add_argument("--channels", type=int, default=1, 
                       help="Number of audio channels")
    
    args = parser.parse_args()
    
    # Create temporary directory if needed
    socket_dir = os.path.dirname(args.socket_path)
    os.makedirs(socket_dir, exist_ok=True)
    
    service = AudioCaptureService(
        socket_path=args.socket_path,
        sample_rate=args.sample_rate,
        channels=args.channels
    )
    
    # Log service initialization
    logger.info(f"Capture service initialized with socket: {args.socket_path}")
    
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