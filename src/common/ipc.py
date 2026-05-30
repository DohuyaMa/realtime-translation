"""Inter-Process Communication module using UNIX sockets for real-time translation system."""

import socket
import struct
import json
import threading
import os
import tempfile
import time
from typing import Any, Optional, Callable, Dict
from loguru import logger


class IPCServer:
    """Server side of IPC communication using UNIX sockets."""
    
    def __init__(self, socket_path: str):
        """Initialize IPC server.
        
        Args:
            socket_path: Path to the UNIX socket
        """
        self.socket_path = socket_path
        self.server_socket: Optional[socket.socket] = None
        self.running = False
        self.handlers: Dict[str, Callable] = {}
        self._client_count = 0
        
    def register_handler(self, message_type: str, handler: Callable):
        """Register a handler for a specific message type.
        
        Args:
            message_type: Type of message to handle
            handler: Function to handle the message
        """
        self.handlers[message_type] = handler
        
    def start(self):
        """Start the IPC server."""
        if os.path.exists(self.socket_path):
            logger.debug(f"Removing stale socket: {self.socket_path}")
            os.remove(self.socket_path)
            
        self.server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server_socket.bind(self.socket_path)
        self.server_socket.listen(5)
        self.running = True
        
        logger.info(f"IPC Server started on {self.socket_path}")
        
        # Start listening in a separate thread
        self.server_thread = threading.Thread(target=self._listen, daemon=True)
        self.server_thread.start()
        
    def _listen(self):
        """Listen for incoming connections and messages."""
        while self.running:
            try:
                conn, addr = self.server_socket.accept()
                self._client_count += 1
                client_id = self._client_count
                logger.debug(f"IPC client #{client_id} connected")
                client_thread = threading.Thread(
                    target=self._handle_client, 
                    args=(conn, client_id), 
                    daemon=True
                )
                client_thread.start()
            except Exception as e:
                if self.running:
                    logger.exception(f"Error accepting connection: {e}")
                    
    def _handle_client(self, conn: socket.socket, client_id: int):
        """Handle messages from a client connection."""
        try:
            while self.running:
                # Read message length (4 bytes)
                length_data = conn.recv(4)
                if not length_data:
                    break
                    
                message_length = struct.unpack('!I', length_data)[0]
                
                # Read the message data
                message_data = b''
                while len(message_data) < message_length:
                    chunk = conn.recv(message_length - len(message_data))
                    if not chunk:
                        break
                    message_data += chunk
                    
                # Parse the message
                message = json.loads(message_data.decode('utf-8'))
                message_type = message.get('type')
                logger.debug(f"IPC client #{client_id} request: type={message_type} size={message_length}")
                
                # Handle the message
                if message_type in self.handlers:
                    t0 = time.monotonic()
                    response = self.handlers[message_type](message)
                    elapsed = time.monotonic() - t0
                    if elapsed > 1.0:
                        logger.debug(f"IPC handler {message_type} took {elapsed:.3f}s (client #{client_id})")
                    if response is not None:
                        self._send_response(conn, response)
                else:
                    logger.warning(f"Unknown message type: {message_type} (client #{client_id})")
                    
        except json.JSONDecodeError as e:
            logger.error(f"IPC client #{client_id} sent invalid JSON: {e}")
        except struct.error as e:
            logger.error(f"IPC client #{client_id} protocol error: {e}")
        except Exception as e:
            logger.exception(f"Error handling client #{client_id}: {e}")
        finally:
            conn.close()
            logger.debug(f"IPC client #{client_id} disconnected")
            
    def _send_response(self, conn: socket.socket, response: Any):
        """Send a response back to the client."""
        try:
            response_data = json.dumps(response).encode('utf-8')
            response_length = len(response_data)
            conn.sendall(struct.pack('!I', response_length))
            conn.sendall(response_data)
        except Exception as e:
            logger.exception(f"Error sending response (size={response_length}): {e}")
            
    def stop(self):
        """Stop the IPC server."""
        self.running = False
        if self.server_socket:
            self.server_socket.close()
        if os.path.exists(self.socket_path):
            os.remove(self.socket_path)
        logger.info("IPC Server stopped")


class IPCClient:
    """Client side of IPC communication using UNIX sockets."""
    
    def __init__(self, socket_path: str):
        """Initialize IPC client.
        
        Args:
            socket_path: Path to the UNIX socket
        """
        self.socket_path = socket_path
        self.socket: Optional[socket.socket] = None
        
    def connect(self):
        """Connect to the IPC server."""
        if not os.path.exists(self.socket_path):
            logger.error(f"Cannot connect: socket not found: {self.socket_path}")
            raise FileNotFoundError(f"IPC socket not found: {self.socket_path}")
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(5.0)
        try:
            self.socket.connect(self.socket_path)
            logger.info(f"IPC Client connected to {self.socket_path}")
        except (ConnectionRefusedError, FileNotFoundError) as e:
            logger.error(f"Connection to {self.socket_path} refused/missing: {e}")
            raise
        except Exception as e:
            logger.exception(f"Failed to connect to {self.socket_path}: {e}")
            raise
        
    def send_message(self, message_type: str, data: Any) -> Optional[Any]:
        """Send a message to the server and wait for response.
        
        Args:
            message_type: Type of message to send
            data: Data to send with the message
            
        Returns:
            Response from server, or None if no response expected
        """
        if not self.socket:
            raise RuntimeError(f"Not connected to server (cannot send {message_type})")
            
        # Prepare the message
        message = {
            'type': message_type,
            'data': data
        }
        
        message_data = json.dumps(message).encode('utf-8')
        message_length = len(message_data)
        
        # Send message length followed by message data
        try:
            self.socket.sendall(struct.pack('!I', message_length))
            self.socket.sendall(message_data)
        except BrokenPipeError:
            logger.error(f"Broken pipe sending {message_type} to {self.socket_path}")
            raise
        except Exception as e:
            logger.exception(f"Failed to send {message_type} to {self.socket_path}: {e}")
            raise
        
        # Receive response if any
        try:
            length_data = self.socket.recv(4)
            if length_data:
                response_length = struct.unpack('!I', length_data)[0]
                response_data = b''
                while len(response_data) < response_length:
                    chunk = self.socket.recv(response_length - len(response_data))
                    if not chunk:
                        break
                    response_data += chunk
                    
                result = json.loads(response_data.decode('utf-8'))
                return result
        except socket.timeout:
            logger.debug(f"IPC {message_type} timed out waiting for response")
            return None  # No response expected or timeout
            
    def send_audio_data(self, audio_data: bytes) -> Optional[Any]:
        """Send audio data to the server.
        
        Args:
            audio_data: Raw audio data to send
            
        Returns:
            Response from server
        """
        # Encode audio data as base64 string
        import base64
        audio_b64 = base64.b64encode(audio_data).decode('utf-8')
        return self.send_message('audio_data', {
            'data': audio_b64,
            'format': 's16le',
            'sample_rate': 16000,
            'channels': 1
        })
        
    def send_text_data(self, text: str) -> Optional[Any]:
        """Send text data to the server.
        
        Args:
            text: Text to send
            
        Returns:
            Response from server
        """
        return self.send_message('text_data', {
            'text': text
        })
        
    def disconnect(self):
        """Disconnect from the server."""
        if self.socket:
            try:
                self.socket.close()
            except Exception as e:
                logger.debug(f"Error closing IPC socket: {e}")
            self.socket = None
        logger.info(f"IPC Client disconnected from {self.socket_path}")