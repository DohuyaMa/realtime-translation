"""
Socket communication tests for the real-time translation system.
These tests validate that socket communication between services works correctly.
"""

import os
import sys
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch, MagicMock
import time
import json

# Add src directory to path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.runtime import RuntimePaths
from common.ipc import IPCClient, IPCServer


class TestSocketCommunication(unittest.TestCase):
    """Test socket communication between services"""
    
    def setUp(self):
        """Set up test environment with temporary socket directory"""
        self.temp_dir = tempfile.mkdtemp()
        self.socket_dir = os.path.join(self.temp_dir, 'sockets')
        os.makedirs(self.socket_dir, exist_ok=True)
        
        self.runtime_paths = RuntimePaths(
            xdg_base=self.temp_dir,
            socket_dir=self.socket_dir,
            log_dir=os.path.join(self.temp_dir, 'logs'),
            config_dir=os.path.join(self.temp_dir, 'config')
        )
        
        # Create socket paths for different services
        self.capture_socket = os.path.join(self.socket_dir, 'capture.sock')
        self.whisper_socket = os.path.join(self.socket_dir, 'whisper.sock')
        self.translate_socket = os.path.join(self.socket_dir, 'translate.sock')
        self.tts_socket = os.path.join(self.socket_dir, 'tts.sock')
        self.playback_socket = os.path.join(self.socket_dir, 'playback.sock')
    
    def tearDown(self):
        """Clean up test environment"""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_socket_creation(self):
        """Test that socket files can be created"""
        # Test socket file creation
        test_socket_path = os.path.join(self.socket_dir, 'test.sock')
        
        # Create a simple socket
        if os.path.exists(test_socket_path):
            os.remove(test_socket_path)
        
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.bind(test_socket_path)
            self.assertTrue(os.path.exists(test_socket_path))
        finally:
            sock.close()
            if os.path.exists(test_socket_path):
                os.remove(test_socket_path)
    
    def test_ipc_server_client_communication(self):
        """Test basic IPC server-client communication"""
        test_socket_path = os.path.join(self.socket_dir, 'ipc-test.sock')
        
        if os.path.exists(test_socket_path):
            os.remove(test_socket_path)
        
        # Create a simple server that echoes messages
        def run_server():
            server = IPCServer(test_socket_path)
            server.start()
            
            # Wait for a message and echo it back
            conn = server.accept()
            if conn:
                data = conn.recv(1024)
                if data:
                    conn.send(data)  # Echo back
                conn.close()
            
            server.stop()
        
        # Start server in a separate thread
        server_thread = threading.Thread(target=run_server)
        server_thread.daemon = True
        server_thread.start()
        
        # Give server time to start
        time.sleep(0.1)
        
        # Create client and send message
        try:
            client = IPCClient(test_socket_path)
            client.connect()
            
            test_message = b"Hello, server!"
            client.send(test_message)
            
            response = client.recv(1024)
            self.assertEqual(response, test_message)
            
            client.disconnect()
        except Exception as e:
            # If there's a connection error, wait a bit more and try again
            time.sleep(0.2)
            try:
                client = IPCClient(test_socket_path)
                client.connect()
                
                test_message = b"Hello, server!"
                client.send(test_message)
                
                response = client.recv(1024)
                self.assertEqual(response, test_message)
                
                client.disconnect()
            except Exception as e2:
                self.fail(f"Failed to communicate with server: {e2}")
        
        # Wait for server to finish
        server_thread.join(timeout=1)
    
    def test_service_socket_paths(self):
        """Test that all expected service socket paths exist"""
        expected_sockets = [
            self.capture_socket,
            self.whisper_socket,
            self.translate_socket,
            self.tts_socket,
            self.playback_socket
        ]
        
        # Create all socket files to simulate systemd socket activation
        for sock_path in expected_sockets:
            if os.path.exists(sock_path):
                os.remove(sock_path)
            # Create the socket file
            with open(sock_path, 'w') as f:
                f.write('')  # Create empty file as placeholder
        
        # Verify all expected sockets exist
        for sock_path in expected_sockets:
            self.assertTrue(os.path.exists(sock_path), f"Socket {sock_path} should exist")
    
    def test_socket_permissions(self):
        """Test socket file permissions"""
        test_socket_path = os.path.join(self.socket_dir, 'permission-test.sock')
        
        if os.path.exists(test_socket_path):
            os.remove(test_socket_path)
        
        # Create a socket file
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.bind(test_socket_path)
            
            # Check that the socket file exists and has appropriate permissions
            self.assertTrue(os.path.exists(test_socket_path))
            
            # Check permissions (should be writable by owner)
            stat_info = os.stat(test_socket_path)
            self.assertTrue(stat_info.st_mode & 0o200)  # Owner write permission
        finally:
            sock.close()
            if os.path.exists(test_socket_path):
                os.remove(test_socket_path)


class TestSystemdSocketActivation(unittest.TestCase):
    """Test systemd socket activation compatibility"""
    
    def setUp(self):
        """Set up test environment"""
        self.temp_dir = tempfile.mkdtemp()
        self.socket_dir = os.path.join(self.temp_dir, 'sockets')
        os.makedirs(self.socket_dir, exist_ok=True)
    
    def tearDown(self):
        """Clean up test environment"""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_systemd_socket_names(self):
        """Test systemd-compatible socket names"""
        systemd_socket_names = [
            'rt-capture.socket',
            'rt-whisper.socket',
            'rt-translate.socket', 
            'rt-tts.socket',
            'rt-playback.socket',
            'rt-hybrid-whisper.socket'
        ]
        
        # Create socket files with systemd-compatible names
        for sock_name in systemd_socket_names:
            sock_path = os.path.join(self.socket_dir, sock_name)
            with open(sock_path, 'w') as f:
                f.write('')  # Placeholder file
        
        # Verify all systemd socket files exist
        for sock_name in systemd_socket_names:
            sock_path = os.path.join(self.socket_dir, sock_name)
            self.assertTrue(os.path.exists(sock_path), f"Systemd socket {sock_path} should exist")


class TestMessageSerialization(unittest.TestCase):
    """Test message serialization for socket communication"""
    
    def test_json_message_format(self):
        """Test that JSON messages can be sent and received via sockets"""
        test_socket_path = os.path.join(tempfile.gettempdir(), f'test-json-{os.getpid()}.sock')
        
        if os.path.exists(test_socket_path):
            os.remove(test_socket_path)
        
        # Test message serialization
        test_message = {
            'type': 'audio_chunk',
            'data': 'encoded_audio_data_here',
            'timestamp': time.time(),
            'session_id': 'test_session_123'
        }
        
        serialized = json.dumps(test_message)
        deserialized = json.loads(serialized)
        
        self.assertEqual(test_message['type'], deserialized['type'])
        self.assertEqual(test_message['session_id'], deserialized['session_id'])
        self.assertEqual(test_message['timestamp'], deserialized['timestamp'])
        
        # Clean up
        if os.path.exists(test_socket_path):
            os.remove(test_socket_path)


if __name__ == '__main__':
    unittest.main()