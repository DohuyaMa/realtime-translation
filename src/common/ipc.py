"""Inter-Process Communication module using UNIX sockets for real-time translation system."""

import socket
import struct
import json
import threading
import os
import tempfile
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
            os.remove(self.socket_path)
            
        self.server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server_socket.bind(self.socket_path)
        self.server_socket.listen(1)
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
                client_thread = threading.Thread(
                    target=self._handle_client, 
                    args=(conn,), 
                    daemon=True
                )
                client_thread.start()
            except Exception as e:
                if self.running:
                    logger.error(f"Error accepting connection: {e}")
                    
    def _handle_client(self, conn: socket.socket):
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
                
                # Handle the message
                message_type = message.get('type')
                if message_type in self.handlers:
                    response = self.handlers[message_type](message)
                    if response is not None:
                        self._send_response(conn, response)
                else:
                    logger.warning(f"Unknown message type: {message_type}")
                    
        except Exception as e:
            logger.error(f"Error handling client: {e}")
        finally:
            conn.close()
            
    def _send_response(self, conn: socket.socket, response: Any):
        """Send a response back to the client."""
        try:
            response_data = json.dumps(response).encode('utf-8')
            response_length = len(response_data)
            conn.sendall(struct.pack('!I', response_length))
            conn.sendall(response_data)
        except Exception as e:
            logger.error(f"Error sending response: {e}")
            
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
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.connect(self.socket_path)
        logger.info(f"IPC Client connected to {self.socket_path}")
        
    def send_message(self, message_type: str, data: Any) -> Optional[Any]:
        """Send a message to the server and wait for response.
        
        Args:
            message_type: Type of message to send
            data: Data to send with the message
            
        Returns:
            Response from server, or None if no response expected
        """
        if not self.socket:
            raise RuntimeError("Not connected to server")
            
        # Prepare the message
        message = {
            'type': message_type,
            'data': data
        }
        
        message_data = json.dumps(message).encode('utf-8')
        message_length = len(message_data)
        
        # Send message length followed by message data
        self.socket.sendall(struct.pack('!I', message_length))
        self.socket.sendall(message_data)
        
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
                    
                return json.loads(response_data.decode('utf-8'))
        except socket.timeout:
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
            self.socket.close()
        logger.info("IPC Client disconnected")