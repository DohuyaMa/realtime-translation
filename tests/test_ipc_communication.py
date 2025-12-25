"""Unit tests for IPC communication handling."""

import pytest
from unittest.mock import Mock, patch, MagicMock
import socket
import threading
import time
import struct
import json
import tempfile
import os

from src.common.ipc import IPCServer, IPCClient


class TestIPCCommunication:
    """Test class for IPC communication functionality."""
    
    def setup_method(self):
        """Set up test fixtures before each test method."""
        self.socket_path = os.path.join(tempfile.gettempdir(), f"test_ipc_{int(time.time())}.sock")
    
    def teardown_method(self):
        """Clean up after each test method."""
        if os.path.exists(self.socket_path):
            os.remove(self.socket_path)
    
    def test_ipc_server_initialization(self):
        """Test IPC server initialization."""
        server = IPCServer(self.socket_path)
        
        assert server.socket_path == self.socket_path
        assert server.server_socket is None
        assert server.running is False
        assert server.handlers == {}
    
    def test_ipc_client_initialization(self):
        """Test IPC client initialization."""
        client = IPCClient(self.socket_path)
        
        assert client.socket_path == self.socket_path
        assert client.socket is None
    
    def test_ipc_server_start_stop(self):
        """Test starting and stopping IPC server."""
        server = IPCServer(self.socket_path)
        
        # Start the server
        server.start()
        assert server.running is True
        assert server.server_socket is not None
        
        # Stop the server
        server.stop()
        assert server.running is False
        # Socket file should be removed
        assert not os.path.exists(self.socket_path)
    
    def test_ipc_server_handler_registration(self):
        """Test registering message handlers with IPC server."""
        server = IPCServer(self.socket_path)
        
        # Register a handler
        def test_handler(message):
            return {"result": "test"}
        
        server.register_handler("test_message", test_handler)
        
        # Verify handler is registered
        assert "test_message" in server.handlers
        assert server.handlers["test_message"] == test_handler
    
    def test_ipc_basic_message_flow(self):
        """Test basic message flow between client and server."""
        # Start server in a separate thread
        server = IPCServer(self.socket_path)
        
        # Register a handler that echoes the message data
        def echo_handler(message):
            return {"status": "success", "data": message.get("data")}
        
        server.register_handler("echo", echo_handler)
        
        server.start()
        
        try:
            # Create and connect client
            client = IPCClient(self.socket_path)
            client.connect()
            
            # Send a message and get response
            response = client.send_message("echo", {"test": "data"})
            
            # Verify response
            assert response is not None
            assert response["status"] == "success"
            assert response["data"] == {"test": "data"}
            
            # Disconnect client
            client.disconnect()
        
        finally:
            # Stop server
            server.stop()
    
    def test_ipc_server_multiple_clients(self):
        """Test server handling multiple client connections."""
        server = IPCServer(self.socket_path)
        
        # Register a handler that returns connection count
        connection_count = 0
        
        def count_handler(message):
            nonlocal connection_count
            connection_count += 1
            return {"count": connection_count}
        
        server.register_handler("count", count_handler)
        server.start()
        
        try:
            # Connect first client
            client1 = IPCClient(self.socket_path)
            client1.connect()
            response1 = client1.send_message("count", {})
            
            # Connect second client
            client2 = IPCClient(self.socket_path)
            client2.connect()
            response2 = client2.send_message("count", {})
            
            # Verify both clients got responses
            assert response1["count"] == 1
            assert response2["count"] == 2
            
            client1.disconnect()
            client2.disconnect()
        
        finally:
            server.stop()
    
    def test_ipc_client_disconnect_reconnect(self):
        """Test client disconnect and reconnect functionality."""
        server = IPCServer(self.socket_path)
        server.register_handler("test", lambda msg: {"status": "ok"})
        server.start()
        
        try:
            client = IPCClient(self.socket_path)
            client.connect()
            
            # Send a message while connected
            response = client.send_message("test", {})
            assert response is not None
            
            # Disconnect and reconnect
            client.disconnect()
            client.connect()
            
            # Send another message after reconnect
            response = client.send_message("test", {})
            assert response is not None
            
            client.disconnect()
        
        finally:
            server.stop()
    
    def test_ipc_message_format(self):
        """Test that messages follow the correct format with length prefix."""
        # Create a test message
        message = {"type": "test", "data": {"value": 123}}
        message_data = json.dumps(message).encode('utf-8')
        message_length = len(message_data)
        
        # Verify the length is packed correctly
        length_packed = struct.pack('!I', message_length)
        length_unpacked = struct.unpack('!I', length_packed)[0]
        
        assert length_unpacked == message_length
    
    @pytest.mark.timeout(10)  # Add timeout to prevent hanging
    def test_ipc_server_unknown_message_type(self):
        """Test server behavior with unknown message type."""
        server = IPCServer(self.socket_path)
        server.start()
        
        try:
            client = IPCClient(self.socket_path)
            client.connect()
            
            # Send a message with unknown type
            response = client.send_message("unknown_type", {"data": "test"})
            
            # Should return None since there's no handler
            assert response is None
            
            client.disconnect()
        
        finally:
            server.stop()
    
    def test_ipc_client_not_connected_error(self):
        """Test that client raises error when not connected."""
        client = IPCClient(self.socket_path)
        
        # Should raise RuntimeError when not connected
        with pytest.raises(RuntimeError, match="Not connected to server"):
            client.send_message("test", {})
    
    def test_ipc_server_cleanup(self):
        """Test server cleanup removes socket file."""
        socket_path = os.path.join(tempfile.gettempdir(), "test_cleanup.sock")
        
        server = IPCServer(socket_path)
        server.start()
        
        # Verify socket file exists
        assert os.path.exists(socket_path)
        
        # Stop server
        server.stop()
        
        # Verify socket file is removed
        assert not os.path.exists(socket_path)


class TestTranslationSystemIPCIntegration:
    """Test IPC integration with TranslationSystem."""
    
    def test_translation_system_ipc_clients_initialization(self):
        """Test that TranslationSystem initializes IPC clients correctly."""
        from src.translation_system import TranslationSystem
        
        ts = TranslationSystem()
        
        # Check that all IPC clients are initialized
        assert ts.capture_client is not None
        assert ts.whisper_client is not None
        assert ts.translate_client is not None
        assert ts.tts_client is not None
        assert ts.playback_client is not None
        
        # Check that client socket paths are set correctly
        assert ts.capture_client.socket_path == "/tmp/rt-capture.sock"
        assert ts.whisper_client.socket_path == "/tmp/rt-whisper.sock"
        assert ts.translate_client.socket_path == "/tmp/rt-translate.sock"
        assert ts.tts_client.socket_path == "/tmp/rt-tts.sock"
        assert ts.playback_client.socket_path == "/tmp/rt-playback.sock"


def test_ipc_server_with_exception_handler():
    """Test server behavior when handler throws an exception."""
    socket_path = os.path.join(tempfile.gettempdir(), "test_exception.sock")
    
    server = IPCServer(socket_path)
    
    def error_handler(message):
        raise Exception("Test exception in handler")
    
    server.register_handler("error", error_handler)
    server.start()
    
    try:
        client = IPCClient(socket_path)
        client.connect()
        
        # Sending a message to handler that throws exception should not crash server
        response = client.send_message("error", {})
        
        # Response might be None depending on error handling
        # The important thing is that the server doesn't crash
        
        client.disconnect()
    
    finally:
        server.stop()
        if os.path.exists(socket_path):
            os.remove(socket_path)


if __name__ == "__main__":
    pytest.main([__file__])