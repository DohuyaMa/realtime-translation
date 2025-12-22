"""Integration tests for the complete real-time translation system."""

import pytest
from unittest.mock import Mock, patch, MagicMock
import sys
from PyQt6.QtWidgets import QApplication
import time

from src.translation_system import TranslationSystem
from src.ui.main_window import MainWindow
from src.common.ipc import IPCServer, IPCClient


class TestSystemIntegration:
    """Integration tests for the complete system."""
    
    @classmethod
    def setup_class(cls):
        """Set up the QApplication for UI tests."""
        if not QApplication.instance():
            cls.app = QApplication(sys.argv)
        else:
            cls.app = QApplication.instance()
    
    def setup_method(self):
        """Set up test fixtures before each test method."""
        # For integration tests, we'll mock the external dependencies
        # but test the integration between components
        pass
    
    def test_translation_system_with_mock_services(self):
        """Test TranslationSystem integration with mocked services."""
        # Mock all the service IPC clients
        with patch('src.translation_system.IPCClient') as mock_ipc_client:
            # Create mock clients that return expected responses
            mock_capture_client = Mock()
            mock_capture_client.send_message.return_value = {'status': 'success'}
            mock_capture_client.connect.return_value = None
            
            mock_whisper_client = Mock()
            mock_whisper_client.send_message.return_value = {'status': 'success', 'data': {'text': 'test'}}
            mock_whisper_client.connect.return_value = None
            
            mock_translate_client = Mock()
            mock_translate_client.send_message.return_value = {'status': 'success', 'data': {'translated_text': 'translated test'}}
            mock_translate_client.connect.return_value = None
            
            mock_tts_client = Mock()
            mock_tts_client.send_message.return_value = {'status': 'success', 'data': {'audio_data': 'mock_audio'}}
            mock_tts_client.connect.return_value = None
            
            mock_playback_client = Mock()
            mock_playback_client.send_message.return_value = {'status': 'success'}
            mock_playback_client.connect.return_value = None
            
            # Configure the mock IPCClient to return the appropriate mock based on socket path
            def mock_ipc_side_effect(socket_path):
                if 'capture' in socket_path:
                    return mock_capture_client
                elif 'whisper' in socket_path:
                    return mock_whisper_client
                elif 'translate' in socket_path:
                    return mock_translate_client
                elif 'tts' in socket_path:
                    return mock_tts_client
                elif 'playback' in socket_path:
                    return mock_playback_client
                return Mock()  # fallback
            
            mock_ipc_client.side_effect = mock_ipc_side_effect
            
            # Create translation system
            ts = TranslationSystem()
            
            # Test that all clients were created
            assert ts.capture_client is not None
            assert ts.whisper_client is not None
            assert ts.translate_client is not None
            assert ts.tts_client is not None
            assert ts.playback_client is not None
            
            # Test start/stop functionality
            ts.start()
            mock_capture_client.send_message.assert_called_with('start_capture', {})
            
            ts.stop()
            # Verify stop was called
            
            # Test language setting
            ts.set_languages('en', 'es')
            # Verify language setting messages were sent to appropriate services
            
            # Test stats collection
            stats = ts.get_stats()
            assert 'running' in stats
            assert 'translation_enabled' in stats
    
    def test_main_window_with_translation_system(self):
        """Test MainWindow integration with TranslationSystem."""
        with patch('src.ui.main_window.TranslationSystem') as mock_translation_system_class:
            # Create a mock translation system instance
            mock_ts = Mock()
            mock_ts.get_stats.return_value = {
                'running': False,
                'translation_enabled': True,
                'source_language': 'auto',
                'target_language': 'en',
                'audio_level': 0.5,
                'is_speech': False,
                'capture_connected': True,
                'whisper_connected': True,
                'translate_connected': True,
                'tts_connected': True,
                'playback_connected': True
            }
            mock_ts.get_audio_devices.return_value = {
                'inputs': [
                    {'name': 'default_input', 'description': 'Default Input Device'}
                ],
                'outputs': [
                    {'name': 'default_output', 'description': 'Default Output Device'}
                ]
            }
            mock_ts.set_input_device.return_value = {'status': 'success'}
            mock_ts.start_service.return_value = True
            mock_ts.stop_service.return_value = True
            mock_ts.all_services_connected.return_value = True
            
            mock_translation_system_class.return_value = mock_ts
            
            # Create main window
            window = MainWindow()
            
            # Verify that translation system was initialized
            assert window.translation_system is not None
            
            # Test UI update method
            window.update_ui()
            # This should run without errors and update the UI elements
            
            # Test service status display update
            window.update_service_status_display(mock_ts.get_stats.return_value)
            # This should update the service status indicators
            
            # Test translation toggle
            initial_state = window.is_translating
            window.toggle_translation()
            if initial_state:
                mock_ts.stop.assert_called()
            else:
                mock_ts.start.assert_called()
            
            window.close()
    
    def test_translation_pipeline_flow(self):
        """Test the complete translation pipeline flow."""
        with patch('src.translation_system.IPCClient') as mock_ipc_client:
            # Set up mock clients for the pipeline
            mock_capture = Mock()
            mock_capture.send_message.return_value = {'status': 'success'}
            
            mock_whisper = Mock()
            mock_whisper.send_message.return_value = {
                'status': 'success', 
                'data': {'text': 'Hello world'}
            }
            
            mock_translate = Mock()
            mock_translate.send_message.return_value = {
                'status': 'success',
                'data': {'translated_text': 'Hola mundo'}
            }
            
            mock_tts = Mock()
            mock_tts.send_message.return_value = {
                'status': 'success',
                'data': {'audio_data': 'mock_audio_data', 'duration': 1.5}
            }
            
            mock_playback = Mock()
            mock_playback.send_message.return_value = {'status': 'success'}
            
            def mock_ipc_side_effect(socket_path):
                if 'capture' in socket_path:
                    return mock_capture
                elif 'whisper' in socket_path:
                    return mock_whisper
                elif 'translate' in socket_path:
                    return mock_translate
                elif 'tts' in socket_path:
                    return mock_tts
                elif 'playback' in socket_path:
                    return mock_playback
                return Mock()
            
            mock_ipc_client.side_effect = mock_ipc_side_effect
            
            # Create translation system
            ts = TranslationSystem()
            
            # Simulate processing an audio chunk
            import numpy as np
            mock_audio_data = np.array([0.1, 0.2, 0.3])  # Mock audio data
            
            # This should trigger the complete pipeline
            # Note: In a real test, we'd check that all services were called in sequence
            ts.process_audio_chunk(mock_audio_data)
            
            # Verify that each service in the pipeline was called appropriately
            mock_whisper.send_message.assert_called()
            mock_translate.send_message.assert_called()
            mock_tts.send_message.assert_called()
            mock_playback.send_message.assert_called()
    
    def test_service_control_integration(self):
        """Test service control integration between UI and TranslationSystem."""
        with patch('src.ui.main_window.TranslationSystem') as mock_translation_system_class:
            mock_ts = Mock()
            mock_ts.get_stats.return_value = {
                'running': False,
                'translation_enabled': True,
                'source_language': 'auto',
                'target_language': 'en',
                'capture_connected': True,
                'whisper_connected': True,
                'translate_connected': True,
                'tts_connected': True,
                'playback_connected': True
            }
            mock_ts.get_audio_devices.return_value = {
                'inputs': [{'name': 'mic1', 'description': 'Microphone 1'}],
                'outputs': [{'name': 'speaker1', 'description': 'Speaker 1'}]
            }
            mock_ts.start_service.return_value = True
            mock_ts.stop_service.return_value = True
            mock_ts.set_input_device.return_value = {'status': 'success'}
            
            mock_translation_system_class.return_value = mock_ts
            
            # Create main window
            window = MainWindow()
            
            # Test individual service controls
            for service in ['capture', 'whisper', 'translate', 'tts', 'playback']:
                # Initially, button should show "Stop" since we mocked services as connected
                button = window.service_buttons[service]
                
                # Toggle the service
                window.toggle_service(service)
                
                # Verify that the appropriate method was called on the translation system
                if button.text() == "Start":
                    mock_ts.stop_service.assert_called_with(service)
                else:
                    mock_ts.start_service.assert_called_with(service)
            
            window.close()
    
    def test_audio_device_integration(self):
        """Test audio device selection integration."""
        with patch('src.ui.main_window.TranslationSystem') as mock_translation_system_class:
            mock_ts = Mock()
            mock_ts.get_stats.return_value = {
                'running': False,
                'translation_enabled': True,
                'source_language': 'auto',
                'target_language': 'en'
            }
            mock_ts.get_audio_devices.return_value = {
                'inputs': [
                    {'name': 'device1', 'description': 'Input Device 1'},
                    {'name': 'device2', 'description': 'Input Device 2'}
                ],
                'outputs': [
                    {'name': 'output1', 'description': 'Output Device 1'}
                ]
            }
            mock_ts.set_input_device.return_value = {'status': 'success'}
            
            mock_translation_system_class.return_value = mock_ts
            
            # Create main window
            window = MainWindow()
            
            # Verify that audio devices were populated
            assert window.input_device_combo.count() == 2  # Two input devices
            assert window.output_device_combo.count() == 1  # One output device
            
            # Test changing input device
            window.input_device_combo.setCurrentIndex(1)  # Select second device
            
            # This should trigger on_input_device_changed which calls set_input_device
            # Verify that set_input_device was called with the correct device name
            mock_ts.set_input_device.assert_called_with('device2')
            
            window.close()


def test_complete_system_initialization():
    """Test that the complete system initializes without errors."""
    # This test ensures that all components can be imported and initialized
    # without external dependencies causing issues
    
    # Test TranslationSystem creation with mocked dependencies
    with patch('src.translation_system.IPCClient'):
        with patch('src.translation_system.AudioRouter'):
            ts = TranslationSystem()
            assert ts is not None
    
    # Test MainWindow creation with mocked TranslationSystem
    with patch('src.ui.main_window.TranslationSystem') as mock_ts_class:
        mock_ts = Mock()
        mock_ts.get_stats.return_value = {
            'running': False,
            'translation_enabled': True,
            'source_language': 'auto',
            'target_language': 'en'
        }
        mock_ts.get_audio_devices.return_value = {'inputs': [], 'outputs': []}
        mock_ts_class.return_value = mock_ts
        
        if not QApplication.instance():
            app = QApplication(sys.argv)
        
        window = MainWindow()
        assert window is not None
        window.close()


def test_ipc_integration_with_services():
    """Test IPC integration between services."""
    # This test would normally require actual running services,
    # but we'll test the IPC mechanism with mocked services
    import tempfile
    import os
    
    socket_path = os.path.join(tempfile.gettempdir(), f"integration_test_{int(time.time())}.sock")
    
    try:
        # Test creating an IPC client that would connect to a service
        client = IPCClient(socket_path)
        
        # Without a server running, connection should fail
        # This tests that the IPC mechanism is properly set up
        assert client.socket_path == socket_path
        assert client.socket is None
        
    finally:
        # Clean up
        if os.path.exists(socket_path):
            os.remove(socket_path)


if __name__ == "__main__":
    pytest.main([__file__])