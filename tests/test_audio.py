import pytest
import numpy as np
from src.audio.capture import AudioCapture
from src.audio.routing import AudioRouter
from src.audio.processor import AudioProcessor

def test_audio_capture_initialization():
    """Test audio capture initialization."""
    capture = AudioCapture(
        sample_rate=16000,
        channels=1,
        chunk_size=1024
    )
    assert capture is not None
    assert capture.sample_rate == 16000
    assert capture.channels == 1
    assert capture.chunk_size == 1024

def test_audio_device_listing():
    """Test audio device enumeration."""
    capture = AudioCapture()
    devices = capture.get_input_devices()
    assert isinstance(devices, dict)
    assert len(devices) > 0

def test_audio_routing_virtual_devices():
    """Test virtual audio device creation."""
    router = AudioRouter()
    input_name, output_name = router.create_virtual_devices()
    
    assert input_name == "virtual_input"
    assert output_name == "virtual_output"
    
    status = router.get_virtual_device_status()
    assert status['input']['active']
    assert status['output']['active']
    
    router.cleanup()

def test_audio_processor_speech_detection():
    """Test speech detection in audio processor."""
    processor = AudioProcessor(
        sample_rate=16000,
        silence_threshold=0.01,
        min_speech_duration=0.5
    )
    
    # Generate test audio (sine wave)
    duration = 2.0  # seconds
    t = np.linspace(0, duration, int(16000 * duration))
    audio_data = np.sin(2 * np.pi * 440 * t)  # 440 Hz tone
    
    speech_detected = False
    def on_speech(data):
        nonlocal speech_detected
        speech_detected = True
    
    processor.set_callbacks(speech_callback=on_speech)
    processor.process_chunk(audio_data)
    
    assert speech_detected

def test_audio_levels():
    """Test audio level monitoring."""
    processor = AudioProcessor(sample_rate=16000)
    
    # Generate test audio
    audio_data = np.random.uniform(-1, 1, 16000)
    processor.process_chunk(audio_data)
    
    stats = processor.get_stats()
    assert 'audio_level' in stats
    assert isinstance(stats['audio_level'], float)
    assert 0 <= stats['audio_level'] <= 1

@pytest.mark.integration
def test_audio_pipeline():
    """Test complete audio processing pipeline."""
    # Initialize components
    capture = AudioCapture(sample_rate=16000)
    router = AudioRouter()
    processor = AudioProcessor(sample_rate=16000)
    
    # Set up pipeline
    def process_audio(audio_data):
        processor.process_chunk(audio_data)
    
    capture.set_callback(process_audio)
    
    # Create virtual devices
    router.create_virtual_devices()
    
    # Start capture
    capture.start()
    
    # Wait for some audio
    import time
    time.sleep(2)
    
    # Check results
    stats = processor.get_stats()
    assert stats['running']
    
    # Cleanup
    capture.stop()
    router.cleanup()

def test_error_handling():
    """Test error handling in audio components."""
    # Test invalid device index
    with pytest.raises(Exception):
        AudioCapture(input_device_index=999999)
    
    # Test invalid sample rate
    with pytest.raises(Exception):
        AudioCapture(sample_rate=-1)
    
    # Test cleanup after error
    router = AudioRouter()
    router.cleanup()  # Should not raise exceptions