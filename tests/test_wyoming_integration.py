#!/usr/bin/env python3
"""
Test script to verify Wyoming integration.
This script tests the hybrid whisper service and wyoming client functionality.
"""
import os
import sys
import time
import threading
import subprocess
import signal
import tempfile
import numpy as np
import socket
from loguru import logger

# Add the src directory to the path so we can import the modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from whisper.hybrid_whisper_service import run_server as run_hybrid_server
from whisper.wyoming_client import WyomingWhisperService


def test_wyoming_client_connection():
    """Test Wyoming client connection."""
    print("Testing Wyoming client connection...")
    
    # Try to connect to a mock Wyoming service (this will fail, but tests the client code)
    client = WyomingWhisperService(host="localhost", port=10300)
    
    # This should fail since no Wyoming service is running
    connected = client.connect()
    print(f"Connection attempt result: {connected}")
    
    # Disconnect
    client.disconnect()
    print("Wyoming client test completed.")


def test_hybrid_service():
    """Test hybrid whisper service functionality."""
    print("Testing hybrid whisper service...")
    
    # Create a temporary socket path for testing
    with tempfile.NamedTemporaryFile(delete=False) as temp_sock:
        socket_path = temp_sock.name
    
    # Remove the file so the server can create it
    os.unlink(socket_path)
    
    # Run the hybrid server in a separate thread
    def run_hybrid():
        try:
            run_hybrid_server(
                socket_path=socket_path,
                model_name="tiny",
                device="cpu",
                compute_type="float16",
                use_wyoming=False  # Test with local model first
            )
        except Exception as e:
            logger.error(f"Error running hybrid server: {e}")
    
    server_thread = threading.Thread(target=run_hybrid, daemon=True)
    server_thread.start()
    
    # Give the server time to start
    time.sleep(2)
    
    # Test connecting to the socket
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(socket_path)
        print("Successfully connected to hybrid service")
        sock.close()
    except Exception as e:
        print(f"Failed to connect to hybrid service: {e}")
    
    # Clean up
    if os.path.exists(socket_path):
        os.unlink(socket_path)
    
    print("Hybrid service test completed.")


def test_wyoming_service_integration():
    """Test Wyoming service integration."""
    print("Testing Wyoming service integration...")
    
    # This would require the wyoming-faster-whisper service to be running
    # For now, we'll just test that the classes can be imported and instantiated
    wyoming_service = WyomingWhisperService(host="localhost", port=10300)
    print(f"Wyoming service created: {wyoming_service}")
    print("Wyoming service integration test completed.")


def main():
    """Run all tests."""
    print("Starting Wyoming integration tests...")
    
    test_wyoming_client_connection()
    print()
    
    test_hybrid_service()
    print()
    
    test_wyoming_service_integration()
    print()
    
    print("All Wyoming integration tests completed!")


if __name__ == "__main__":
    main()