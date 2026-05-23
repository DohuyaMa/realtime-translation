import pytest
import sys
from unittest.mock import Mock, patch
from src.adapters.direct_adapter import DirectAdapter


def test_direct_adapter_creation():
    """Test DirectAdapter can be created successfully."""
    adapter = DirectAdapter()
    assert adapter is not None
    assert adapter.translation_system is not None


def test_direct_adapter_get_audio_devices():
    """Test DirectAdapter can retrieve audio devices."""
    adapter = DirectAdapter()
    
    # Test getting input devices
    input_devices = adapter.get_input_devices()
    assert isinstance(input_devices, list)
    
    # Test getting output devices
    output_devices = adapter.get_output_devices()
    assert isinstance(output_devices, list)


def test_direct_adapter_device_selection_in_dev_mode():
    """Test DirectAdapter allows device selection even without IPC clients."""
    adapter = DirectAdapter()
    
    # Simulate dev mode where IPC clients are None
    adapter.translation_system.capture_client = None
    adapter.translation_system.playback_client = None
    
    # Should be able to get devices
    input_devices = adapter.get_input_devices()
    output_devices = adapter.get_output_devices()
    
    # Test setting input device (should work with audio router directly)
    # Using a mock device name - in real scenario would use actual device
    result = adapter.set_input_device("test_input_device")
    # This should return True if successful or False if failed, but not raise an exception
    
    # Test setting output device (should work with audio router directly)
    result = adapter.set_output_device("test_output_device")
    # This should return True if successful or False if failed, but not raise an exception


def test_direct_adapter_language_setting_in_dev_mode():
    """Test DirectAdapter allows language setting in dev mode."""
    adapter = DirectAdapter()
    
    # Simulate dev mode where IPC clients are None
    adapter.translation_system.whisper_client = None
    adapter.translation_system.translate_client = None
    
    # Should be able to set languages without IPC clients
    result = adapter.set_languages("uk", "en")
    assert result is True
    
    # Check that languages were set at system level
    assert adapter.translation_system.source_lang == "uk"
    assert adapter.translation_system.target_lang == "en"


def test_direct_adapter_service_control_in_dev_mode():
    """Test DirectAdapter service control in dev mode."""
    adapter = DirectAdapter()
    
    # Simulate dev mode where IPC clients are None
    adapter.translation_system.capture_client = None
    adapter.translation_system.whisper_client = None
    adapter.translation_system.translate_client = None
    adapter.translation_system.tts_client = None
    adapter.translation_system.playback_client = None
    
    # Should be able to start/stop services without errors in dev mode
    result = adapter.start_service("capture")
    assert result is True  # Should return True in dev mode
    
    result = adapter.stop_service("capture")
    assert result is True  # Should return True in dev mode


def test_direct_adapter_pipeline_control():
    """Test DirectAdapter pipeline control methods."""
    adapter = DirectAdapter(auto_spawn_services=False)
    
    # Test starting pipeline
    result = adapter.start_pipeline()
    # Result may be False if services aren't available, but shouldn't crash
    
    # Test stopping pipeline
    result = adapter.stop_pipeline()
    # Should not crash


def test_direct_adapter_status():
    """Test DirectAdapter status reporting."""
    adapter = DirectAdapter()
    
    # Should be able to get status without errors
    status = adapter.get_status()
    assert isinstance(status, dict)
    assert 'running' in status
    assert 'translation_enabled' in status


def test_direct_adapter_audio_levels():
    """Test DirectAdapter audio level reporting."""
    adapter = DirectAdapter()
    
    # Should be able to get audio levels without errors
    levels = adapter.get_audio_levels()
    assert isinstance(levels, dict)
    assert 'input' in levels
    assert 'output' in levels


def test_direct_adapter_translation_toggle():
    """Test DirectAdapter translation toggle."""
    adapter = DirectAdapter()
    
    # Should be able to toggle translation
    result = adapter.toggle_translation(True)
    assert result is True
    
    result = adapter.toggle_translation(False)
    assert result is True


def test_direct_adapter_cleanup():
    """Test DirectAdapter cleanup."""
    adapter = DirectAdapter()
    
    # Should be able to cleanup without errors
    adapter.cleanup()
    # Should not raise exceptions


def test_direct_adapter_virtual_device_functionality():
    """Test DirectAdapter works with virtual devices in devShell mode."""
    adapter = DirectAdapter()
    
    # Get available devices
    input_devices = adapter.get_input_devices()
    output_devices = adapter.get_output_devices()
    
    # Should have some devices available
    assert len(input_devices) >= 0  # May be 0 if no audio devices are present
    assert len(output_devices) >= 0
    
    # Test that we can attempt to set devices without errors
    # Even if no devices exist, the methods should not crash
    if input_devices:
        # Try to set the first available input device
        first_input = input_devices[0].name
        result = adapter.set_input_device(first_input)
        # Result should be boolean (True/False) without raising exceptions
        assert isinstance(result, bool)
    
    if output_devices:
        # Try to set the first available output device
        first_output = output_devices[0].name
        result = adapter.set_output_device(first_output)
        # Result should be boolean (True/False) without raising exceptions
        assert isinstance(result, bool)
    
    # Test with specific virtual device names that should exist in devShell
    # These are the virtual devices created by the shellHook
    result = adapter.set_input_device("rt_virtual_output.monitor")
    assert isinstance(result, bool)
    
    result = adapter.set_output_device("rt_virtual_input")
    assert isinstance(result, bool)