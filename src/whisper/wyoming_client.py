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
        self._bytes_sent = 0
        self._messages_received = 0
        self._connect_attempts = 0

    def connect(self, retries: int = 3, retry_delay: float = 1.0) -> bool:
        """Connect to the Wyoming service with retries."""
        last_error = None
        for attempt in range(1, retries + 1):
            self._connect_attempts += 1
            try:
                self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.socket.settimeout(5.0)
                self.socket.connect((self.host, self.port))
                self.socket.settimeout(None)
                self.connected = True
                
                # Start receiving thread
                self.is_receiving = True
                self.receive_thread = threading.Thread(target=self._receive_loop, daemon=True)
                self.receive_thread.start()
                
                logger.info(f"Connected to Wyoming at {self.host}:{self.port} (attempt {attempt})")
                return True
            except socket.timeout:
                last_error = f"Connection timeout (attempt {attempt}/{retries})"
                logger.warning(f"Wyoming connection timeout {self.host}:{self.port} (attempt {attempt}/{retries})")
            except ConnectionRefusedError:
                last_error = f"Connection refused (attempt {attempt}/{retries})"
                logger.warning(f"Wyoming connection refused {self.host}:{self.port} (attempt {attempt}/{retries})")
            except Exception as e:
                last_error = f"{type(e).__name__}: {e} (attempt {attempt}/{retries})"
                logger.warning(f"Wyoming connection failed {self.host}:{self.port}: {last_error}")
            
            if attempt < retries:
                time.sleep(retry_delay)
            if self.socket:
                try:
                    self.socket.close()
                except Exception:
                    pass
                self.socket = None
        
        logger.error(f"Failed to connect to Wyoming at {self.host}:{self.port} after {retries} attempts: {last_error}")
        self.connected = False
        return False

    def disconnect(self):
        """Disconnect from the Wyoming service."""
        self.is_receiving = False
        if self.socket:
            try:
                self.socket.close()
            except Exception as e:
                logger.debug(f"Error closing Wyoming socket: {e}")
            self.socket = None
        self.connected = False
        if self.receive_thread:
            self.receive_thread.join(timeout=1.0)
        logger.info(f"Disconnected from Wyoming at {self.host}:{self.port} "
                     f"(sent={self._bytes_sent}B recv={self._messages_received}msgs)")

    def _receive_loop(self):
        """Receive messages from the Wyoming service."""
        while self.is_receiving and self.connected:
            try:
                # Read message length (4 bytes, little-endian)
                length_bytes = self._read_exact(4)
                if not length_bytes:
                    logger.debug("Wyoming receive loop: connection closed (empty read)")
                    break
                
                length = struct.unpack('<I', length_bytes)[0]
                if length > 10 * 1024 * 1024:  # sanity check: max 10MB
                    logger.error(f"Wyoming message length {length} exceeds sanity limit (10MB)")
                    break
                
                # Read message data
                data = self._read_exact(length)
                if not data:
                    logger.debug("Wyoming receive loop: connection closed (partial read)")
                    break
                
                # Parse as JSON
                try:
                    message = json.loads(data.decode('utf-8'))
                    self._messages_received += 1
                    self._handle_message(message)
                except json.JSONDecodeError as e:
                    logger.error(f"Wyoming invalid JSON ({len(data)} bytes): {data[:200]}... — {e}")
                    
            except socket.timeout:
                logger.debug("Wyoming receive loop: timeout (continuing)")
                continue
            except ConnectionResetError:
                logger.warning(f"Wyoming connection reset at {self.host}:{self.port}")
                break
            except Exception as e:
                if self.is_receiving:
                    logger.exception(f"Wyoming receive loop error: {e}")
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
            logger.error("Not connected to Wyoming service (cannot send audio)")
            return False

        try:
            # Send audio data as Wyoming audio event using the proper protocol
            event_data = {
                "event": "audio",
                "audio": audio_data.hex()
            }
            
            json_data = json.dumps(event_data).encode('utf-8')
            length = len(json_data)
            
            # Send length + data following Wyoming protocol
            self.socket.sendall(struct.pack('<I', length))
            self.socket.sendall(json_data)
            self._bytes_sent += 4 + length
            
            return True
        except BrokenPipeError:
            logger.error(f"Broken pipe sending audio to Wyoming ({self.host}:{self.port}) — connection lost")
            self.connected = False
            return False
        except Exception as e:
            logger.exception(f"Failed to send audio ({len(audio_data)}B) to Wyoming: {e}")
            self.connected = False
            return False

    def send_event(self, event_name: str, data: Optional[Dict[str, Any]] = None) -> bool:
        """Send an event to the Wyoming service using the Wyoming protocol."""
        if not self.connected or not self.socket:
            logger.error(f"Not connected to Wyoming (cannot send event '{event_name}')")
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
            self._bytes_sent += 4 + length
            
            logger.debug(f"Wyoming event '{event_name}' sent ({length}B)")
            return True
        except BrokenPipeError:
            logger.error(f"Broken pipe sending event '{event_name}' to Wyoming — connection lost")
            self.connected = False
            return False
        except Exception as e:
            logger.exception(f"Failed to send event '{event_name}' to Wyoming: {e}")
            self.connected = False
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