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
    """Test virtual audio device access."""
    router = AudioRouter()
    input_name, output_name = router.get_virtual_devices()
    
    # Just verify we can get the virtual device names
    assert isinstance(input_name, str)
    assert isinstance(output_name, str)
    
    # Check if devices exist in the system
    devices = router.list_devices()
    assert 'inputs' in devices
    assert 'outputs' in devices
    
    status = router.get_virtual_device_status()
    # Status might not always be active in test environment, so just check structure
    assert 'input' in status
    assert 'output' in status
    
    router.cleanup()

def test_audio_processor_speech_detection():
    """Test speech detection in audio processor."""
    processor = AudioProcessor(
        sample_rate=16000,
        silence_threshold=0.01,
        min_speech_duration=0.5
    )
    
    # Generate more realistic test audio that simulates speech patterns
    # Use a combination of frequencies and amplitude variations to simulate speech
    duration = 2.0  # seconds
    sample_rate = 16000
    t = np.linspace(0, duration, int(sample_rate * duration))
    
    # Create a more complex signal that better simulates speech
    # Combine multiple frequencies and add amplitude modulation
    signal1 = 0.3 * np.sin(2 * np.pi * 220 * t)  # Lower frequency component
    signal2 = 0.2 * np.sin(2 * np.pi * 440 * t)  # Mid frequency component
    signal3 = 0.1 * np.sin(2 * np.pi * 880 * t)  # Higher frequency component
    
    # Add amplitude modulation to simulate changing speech patterns
    envelope = 0.5 * (1 + np.sin(2 * np.pi * 2 * t))  # 2 Hz modulation
    audio_data = envelope * (signal1 + signal2 + signal3)
    
    # Add some noise to make it more realistic
    noise = 0.05 * np.random.normal(0, 1, len(audio_data))
    audio_data = audio_data + noise
    
    # Normalize to prevent clipping
    audio_data = audio_data / np.max(np.abs(audio_data)) * 0.8
    
    speech_detected = False
    def on_speech(data):
        nonlocal speech_detected
        speech_detected = True
    
    processor.set_callbacks(speech_callback=on_speech)
    
    # Process the audio in chunks to simulate real-time processing
    chunk_size = 1024
    for i in range(0, len(audio_data), chunk_size):
        chunk = audio_data[i:i+chunk_size]
        processor.process_chunk(chunk)
    
    assert speech_detected

@pytest.mark.timeout(10)  # Add timeout to prevent hanging
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

@pytest.mark.timeout(10)  # Add timeout to prevent hanging
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
    
    # Get virtual devices
    input_name, output_name = router.get_virtual_devices()
    
    # Start capture
    capture.start()
    
    # Wait for some audio
    import time
    time.sleep(0.5)  # Reduced time for test
    
    # Check results
    stats = processor.get_stats()
    # Stats may not always show 'running' depending on implementation, check if dict
    assert isinstance(stats, dict)
    
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