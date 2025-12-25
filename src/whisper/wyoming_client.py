"""
Wyoming Protocol Client for connecting to wyoming-faster-whisper service.
"""
import socket
import struct
import json
import threading
import time
import numpy as np
from typing import Optional, Dict, Any, Callable
from loguru import logger


class WyomingWhisperClient:
    """
    Client for connecting to wyoming-faster-whisper service via TCP.
    """
    def __init__(self, host: str = "localhost", port: int = 10300):
        self.host = host
        self.port = port
        self.socket: Optional[socket.socket] = None
        self.connected = False
        self.receive_thread: Optional[threading.Thread] = None
        self.is_receiving = False
        self.on_result: Optional[Callable[[Dict[str, Any]], None]] = None

    def connect(self) -> bool:
        """Connect to the Wyoming service."""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.connect((self.host, self.port))
            self.connected = True
            
            # Start receiving thread
            self.is_receiving = True
            self.receive_thread = threading.Thread(target=self._receive_loop)
            self.receive_thread.daemon = True
            self.receive_thread.start()
            
            logger.info(f"Connected to Wyoming whisper service at {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Wyoming whisper service: {e}")
            self.connected = False
            return False

    def disconnect(self):
        """Disconnect from the Wyoming service."""
        self.is_receiving = False
        if self.socket:
            self.socket.close()
            self.socket = None
        self.connected = False
        if self.receive_thread:
            self.receive_thread.join(timeout=1.0)
        logger.info("Disconnected from Wyoming whisper service")

    def _receive_loop(self):
        """Receive messages from the Wyoming service."""
        while self.is_receiving and self.connected:
            try:
                # Read message length (4 bytes, little-endian)
                length_bytes = self._read_exact(4)
                if not length_bytes:
                    break
                
                length = struct.unpack('<I', length_bytes)[0]
                
                # Read message data
                data = self._read_exact(length)
                if not data:
                    break
                
                # Parse as JSON
                try:
                    message = json.loads(data.decode('utf-8'))
                    self._handle_message(message)
                except json.JSONDecodeError:
                    logger.error(f"Failed to decode JSON message: {data[:100]}...")
                    
            except Exception as e:
                if self.is_receiving:  # Only log error if we're still supposed to be receiving
                    logger.error(f"Error in receive loop: {e}")
                break

    def _read_exact(self, num_bytes: int) -> Optional[bytes]:
        """Read exactly num_bytes from the socket."""
        data = b''
        while len(data) < num_bytes:
            chunk = self.socket.recv(num_bytes - len(data))
            if not chunk:
                return None
            data += chunk
        return data

    def _handle_message(self, message: Dict[str, Any]):
        """Handle incoming message from Wyoming service."""
        if self.on_result:
            self.on_result(message)

    def send_audio(self, audio_data: bytes) -> bool:
        """Send audio data to the Wyoming service."""
        if not self.connected or not self.socket:
            logger.error("Not connected to Wyoming service")
            return False

        try:
            # Send audio data as Wyoming audio event using the proper protocol
            # Wyoming expects audio data to be sent as a specific event
            event_data = {
                "event": "audio",
                "audio": audio_data.hex()  # Wyoming protocol often uses hex encoding for binary data
            }
            
            json_data = json.dumps(event_data).encode('utf-8')
            length = len(json_data)
            
            # Send length + data following Wyoming protocol
            self.socket.sendall(struct.pack('<I', length))
            self.socket.sendall(json_data)
            
            return True
        except Exception as e:
            logger.error(f"Failed to send audio to Wyoming service: {e}")
            return False

    def send_event(self, event_name: str, data: Optional[Dict[str, Any]] = None) -> bool:
        """Send an event to the Wyoming service using the Wyoming protocol."""
        if not self.connected or not self.socket:
            logger.error("Not connected to Wyoming service")
            return False

        try:
            # Create Wyoming event
            event = {
                "event": event_name
            }
            if data:
                event.update(data)
            
            json_data = json.dumps(event).encode('utf-8')
            length = len(json_data)
            
            # Send length + data following Wyoming protocol
            self.socket.sendall(struct.pack('<I', length))
            self.socket.sendall(json_data)
            
            return True
        except Exception as e:
            logger.error(f"Failed to send event to Wyoming service: {e}")
            return False

    def start_recognition(self, language: Optional[str] = None) -> bool:
        """Start a recognition session."""
        data = {"raw": True}  # Process raw audio
        if language:
            data["language"] = language
        return self.send_event("start-recognition", data)

    def stop_recognition(self) -> bool:
        """Stop the current recognition session."""
        return self.send_event("stop-recognition")

    def set_callback(self, callback: Callable[[Dict[str, Any]], None]):
        """Set callback for recognition results."""
        self.on_result = callback


class WyomingWhisperService:
    """
    Wrapper class that implements the same interface as the existing whisper service
    but connects to wyoming-faster-whisper via TCP instead of Unix socket.
    """
    def __init__(self, host: str = "localhost", port: int = 10300):
        self.client = WyomingWhisperClient(host, port)
        self.on_result: Optional[Callable[[Dict[str, Any]], None]] = None
        self.is_running = False

    def connect(self) -> bool:
        """Connect to the Wyoming service."""
        return self.client.connect()

    def disconnect(self):
        """Disconnect from the Wyoming service."""
        self.client.disconnect()

    def start_recognition(self, language: Optional[str] = None):
        """Start a recognition session."""
        return self.client.start_recognition(language)

    def stop_recognition(self):
        """Stop the current recognition session."""
        return self.client.stop_recognition()

    def send_audio(self, audio_data: bytes):
        """Send audio data for recognition."""
        return self.client.send_audio(audio_data)

    def set_callback(self, callback: Callable[[Dict[str, Any]], None]):
        """Set callback for recognition results."""
        self.on_result = callback
        self.client.set_callback(callback)

    def run_server(self, *args, **kwargs):
        """Compatibility method to match the existing whisper service interface."""
        # This is just for compatibility - the Wyoming client connects to an external service
        pass