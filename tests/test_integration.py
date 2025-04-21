import pytest
import numpy as np
import time
import wave
import os
from src.translation_system import TranslationSystem

# Test data paths
TEST_DATA_DIR = os.path.join(os.path.dirname(__file__), 'test_data')
os.makedirs(TEST_DATA_DIR, exist_ok=True)

# Sample test phrases
TEST_PHRASES = {
    'uk': [
        "Добрий день, це тестове повідомлення",
        "Перевірка системи перекладу",
        "Як працює розпізнавання мови"
    ],
    'pl': [
        "Dzień dobry, to jest wiadomość testowa",
        "Sprawdzanie systemu tłumaczenia",
        "Jak działa rozpoznawanie mowy"
    ]
}

@pytest.fixture
def translation_system():
    """Create translation system instance."""
    system = TranslationSystem(
        use_virtual_audio=True,
        sample_rate=16000
    )
    yield system
    system.cleanup()

def create_test_audio(text: str, lang: str, filename: str):
    """Create test audio file using espeak (must be installed).
    
    Args:
        text: Text to synthesize
        lang: Language code
        filename: Output filename
    """
    import subprocess
    
    # Map language codes to espeak voices
    voice_map = {
        'uk': 'ukrainian',
        'pl': 'polish'
    }
    
    # Create wav file using espeak
    subprocess.run([
        'espeak',
        '-v', voice_map[lang],
        '-w', filename,
        text
    ], check=True)

def load_audio_file(filename: str) -> np.ndarray:
    """Load audio file as numpy array."""
    with wave.open(filename, 'rb') as wav:
        return np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)

@pytest.mark.integration
class TestEndToEnd:
    """End-to-end system tests."""
    
    @pytest.mark.parametrize("source_lang", ['uk', 'pl'])
    def test_language_translation(self, translation_system, source_lang):
        """Test translation from Ukrainian/Polish to English."""
        # Set up system
        translation_system.set_languages(source_lang, "en")
        translation_system.start()
        
        results = []
        def collect_result(result):
            results.append(result)
        translation_system.set_status_callback(collect_result)
        
        # Create and process test audio
        test_file = os.path.join(TEST_DATA_DIR, f'test_{source_lang}.wav')
        create_test_audio(TEST_PHRASES[source_lang][0], source_lang, test_file)
        
        audio_data = load_audio_file(test_file)
        translation_system.audio_processor.process_chunk(audio_data)
        
        # Wait for processing
        timeout = 10
        start_time = time.time()
        while not results and time.time() - start_time < timeout:
            time.sleep(0.1)
        
        assert results, f"No translation results after {timeout} seconds"
        assert isinstance(results[0]['text'], str)
        assert len(results[0]['text']) > 0

    def test_real_time_performance(self, translation_system):
        """Test real-time processing performance."""
        translation_system.start()
        
        # Track processing times
        times = []
        def track_time(result):
            times.append(result['processing_time'])
        translation_system.set_status_callback(track_time)
        
        # Process multiple chunks
        chunk_size = 1024
        for _ in range(10):
            audio_data = np.random.uniform(-1, 1, chunk_size)
            translation_system.audio_processor.process_chunk(audio_data)
            time.sleep(0.1)  # Simulate real-time input
        
        # Check performance
        assert len(times) > 0
        avg_time = sum(times) / len(times)
        assert avg_time < 0.5  # Should process faster than real-time

    def test_continuous_translation(self, translation_system):
        """Test continuous translation over time."""
        translation_system.start()
        
        results = []
        def collect_result(result):
            results.append(result)
        translation_system.set_status_callback(collect_result)
        
        # Simulate continuous audio input
        chunk_duration = 0.5  # seconds
        sample_rate = 16000
        chunk_size = int(chunk_duration * sample_rate)
        
        for _ in range(5):  # Test for 2.5 seconds
            audio_data = np.random.uniform(-1, 1, chunk_size)
            translation_system.audio_processor.process_chunk(audio_data)
            time.sleep(chunk_duration)  # Simulate real-time
        
        assert len(results) > 0
        
        # Check continuous operation
        assert translation_system.is_running
        assert translation_system.audio_processor.get_stats()['running']

    def test_error_recovery(self, translation_system):
        """Test system recovery from errors."""
        translation_system.start()
        
        # Test invalid audio data
        try:
            translation_system.audio_processor.process_chunk(np.array([]))
        except Exception as e:
            assert False, f"Should handle invalid audio gracefully: {e}"
        
        # Verify system is still operational
        assert translation_system.is_running
        
        # Test normal operation after error
        audio_data = np.random.uniform(-1, 1, 1024)
        translation_system.audio_processor.process_chunk(audio_data)
        assert translation_system.is_running

    def test_device_switching(self, translation_system):
        """Test switching audio devices."""
        translation_system.start()
        
        # Get available devices
        devices = translation_system.get_audio_devices()
        assert len(devices['inputs']) > 0
        assert len(devices['outputs']) > 0
        
        # Try switching devices
        if len(devices['inputs']) > 1:
            new_device = next(iter(devices['inputs']))
            translation_system.audio_capture.input_device_index = new_device
            
            # Verify system still works
            audio_data = np.random.uniform(-1, 1, 1024)
            translation_system.audio_processor.process_chunk(audio_data)
            assert translation_system.is_running

    @pytest.mark.parametrize("source_lang,target_lang", [
        ('uk', 'en'),
        ('pl', 'en')
    ])
    def test_language_switching(self, translation_system, source_lang, target_lang):
        """Test switching languages during operation."""
        translation_system.start()
        
        # Initial language
        translation_system.set_languages(source_lang, target_lang)
        
        results = []
        def collect_result(result):
            results.append(result)
        translation_system.set_status_callback(collect_result)
        
        # Process audio in both languages
        for phrase in TEST_PHRASES[source_lang]:
            test_file = os.path.join(TEST_DATA_DIR, f'test_{phrase[:10]}.wav')
            create_test_audio(phrase, source_lang, test_file)
            
            audio_data = load_audio_file(test_file)
            translation_system.audio_processor.process_chunk(audio_data)
            time.sleep(1)  # Wait for processing
        
        assert len(results) > 0
        assert all(isinstance(r['text'], str) for r in results)