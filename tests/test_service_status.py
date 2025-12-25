"""Unit tests for service status checking functionality."""

import pytest
from unittest.mock import Mock, patch, MagicMock
import sys
from PySide6.QtWidgets import QApplication

from src.translation_system import TranslationSystem
from src.ui.main_window import MainWindow


class TestServiceStatus:
    """Test class for service status checking functionality."""
    
    @classmethod
    def setup_class(cls):
        """Set up the QApplication for UI tests."""
        if not QApplication.instance():
            cls.app = QApplication(sys.argv)
        else:
            cls.app = QApplication.instance()
    
    def setup_method(self):
        """Set up test fixtures before each test method."""
        # Mock the TranslationSystem to avoid actual service connections
        with patch('src.ui.main_window.TranslationSystem') as mock_translation_system:
            self.mock_translation_system = Mock(spec=TranslationSystem)
            self.mock_translation_system.get_stats.return_value = {
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
            
            mock_translation_system.return_value = self.mock_translation_system
            self.window = MainWindow()
    
    def teardown_method(self):
        """Clean up after each test method."""
        if hasattr(self, 'window'):
            self.window.close()
    
    def test_translation_system_get_stats_with_service_status(self):
        """Test that TranslationSystem.get_stats includes service connection status."""
        # Mock IPC clients
        mock_capture_client = Mock()
        mock_whisper_client = Mock()
        mock_translate_client = Mock()
        mock_tts_client = Mock()
        mock_playback_client = Mock()
        
        # Create a translation system instance directly
        ts = TranslationSystem()
        ts.capture_client = mock_capture_client
        ts.whisper_client = mock_whisper_client
        ts.translate_client = mock_translate_client
        ts.tts_client = mock_tts_client
        ts.playback_client = mock_playback_client
        
        # Mock the send_message method to return success status
        mock_capture_client.send_message.return_value = {'status': 'success', 'data': {}}
        
        # Test that get_stats returns service connection information
        stats = ts.get_stats()
        
        # Verify that service connection status is included in stats
        assert 'capture_connected' in stats
        assert 'whisper_connected' in stats
        assert 'translate_connected' in stats
        assert 'tts_connected' in stats
        assert 'playback_connected' in stats
    
    def test_translation_system_all_services_connected(self):
        """Test the all_services_connected method of TranslationSystem."""
        # Create a translation system instance directly
        ts = TranslationSystem()
        
        # Initially, all clients should be None, so all_services_connected should return False
        assert not ts.all_services_connected()
        
        # Set all clients to Mock objects
        ts.capture_client = Mock()
        ts.whisper_client = Mock()
        ts.translate_client = Mock()
        ts.tts_client = Mock()
        ts.playback_client = Mock()
        
        # Now all_services_connected should return True
        assert ts.all_services_connected()
    
    def test_translation_system_service_control_methods(self):
        """Test the start_service and stop_service methods of TranslationSystem."""
        # Create a translation system instance directly
        ts = TranslationSystem()
        
        # Mock clients
        mock_capture_client = Mock()
        mock_capture_client.send_message.return_value = {'status': 'success'}
        
        ts.capture_client = mock_capture_client
        ts.whisper_client = None
        ts.translate_client = None
        ts.tts_client = None
        ts.playback_client = None
        
        # Test start_service for capture
        result = ts.start_service('capture')
        assert result is True
        mock_capture_client.send_message.assert_called_once_with('start_capture', {})
        
        # Reset mock
        mock_capture_client.reset_mock()
        
        # Test stop_service for capture
        result = ts.stop_service('capture')
        assert result is True
        mock_capture_client.send_message.assert_called_once_with('stop_capture', {})
        
        # Test with non-existent service
        result = ts.start_service('nonexistent')
        assert result is False
        
        # Test with existing service but no client
        result = ts.start_service('whisper')
        assert result is False
    
    def test_translation_system_set_input_device(self):
        """Test the set_input_device method of TranslationSystem."""
        # Create a translation system instance directly
        ts = TranslationSystem()
        
        # Mock client
        mock_capture_client = Mock()
        mock_capture_client.send_message.return_value = {'status': 'success'}
        
        ts.capture_client = mock_capture_client
        
        # Test set_input_device method
        result = ts.set_input_device('test_device')
        assert result is not None  # Should return the response from send_message
        mock_capture_client.send_message.assert_called_once_with('set_input_device', {
            'device_name': 'test_device'
        })
        
        # Test with exception handling
        mock_capture_client.send_message.side_effect = Exception("Connection failed")
        result = ts.set_input_device('test_device')
        assert result is None


def test_translation_system_initialization():
    """Test basic TranslationSystem initialization."""
    ts = TranslationSystem()
    
    # Check that all client attributes are initially None
    assert ts.capture_client is None
    assert ts.whisper_client is None
    assert ts.translate_client is None
    assert ts.tts_client is None
    assert ts.playback_client is None
    
    # Check that initial state is correct
    assert not ts.is_running
    assert ts.translation_enabled


def test_translation_system_get_stats_empty(self):
    """Test TranslationSystem.get_stats when no clients are connected."""
    ts = TranslationSystem()
    
    # Set clients to None explicitly
    ts.capture_client = None
    ts.whisper_client = None
    ts.translate_client = None
    ts.tts_client = None
    ts.playback_client = None
    
    stats = ts.get_stats()
    
    # Verify basic stats exist
    assert 'running' in stats
    assert 'translation_enabled' in stats
    assert 'source_language' in stats
    assert 'target_language' in stats
    
    # Verify service connection stats are False when clients are None
    assert stats.get('capture_connected') is False
    assert stats.get('whisper_connected') is False
    assert stats.get('translate_connected') is False
    assert stats.get('tts_connected') is False
    assert stats.get('playback_connected') is False


if __name__ == "__main__":
    pytest.main([__file__])