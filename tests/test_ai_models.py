import pytest
import numpy as np
from src.models.whisper_recognition import WhisperRecognizer
from src.models.tts_engine import TTSEngine

# Test data
UKRAINIAN_TEXT = "Добрий день, як справи?"
POLISH_TEXT = "Dzień dobry, jak się masz?"
ENGLISH_TEXT = "Hello, how are you?"

@pytest.fixture
def whisper_recognizer():
    """Create Whisper recognizer instance."""
    return WhisperRecognizer(
        model_size="small",
        use_gpu=False  # Use CPU for testing
    )

@pytest.fixture
def tts_engine():
    """Create TTS engine instance."""
    return TTSEngine(
        use_gpu=False  # Use CPU for testing
    )

def test_whisper_ukrainian_recognition(whisper_recognizer):
    """Test Ukrainian speech recognition."""
    # Generate test audio (this is a placeholder - real audio would be needed)
    audio_data = np.zeros(16000)  # 1 second of silence
    
    # Set Ukrainian as source language
    whisper_recognizer.set_languages("uk", "en")
    
    # Process audio
    result = whisper_recognizer._recognize_audio(audio_data)
    
    assert result is not None
    assert isinstance(result['text'], str)
    assert result['language'] == 'uk'

def test_whisper_polish_recognition(whisper_recognizer):
    """Test Polish speech recognition."""
    audio_data = np.zeros(16000)
    whisper_recognizer.set_languages("pl", "en")
    
    result = whisper_recognizer._recognize_audio(audio_data)
    
    assert result is not None
    assert isinstance(result['text'], str)
    assert result['language'] == 'pl'

def test_whisper_auto_language_detection(whisper_recognizer):
    """Test automatic language detection."""
    audio_data = np.zeros(16000)
    whisper_recognizer.set_languages("auto", "en")
    
    result = whisper_recognizer._recognize_audio(audio_data)
    
    assert result is not None
    assert 'language' in result

def test_tts_synthesis(tts_engine):
    """Test English TTS synthesis."""
    audio_data = tts_engine._synthesize_text(
        ENGLISH_TEXT,
        {'speed': 1.0, 'pitch': 0.0, 'energy': 1.0}
    )
    
    assert audio_data is not None
    assert isinstance(audio_data, np.ndarray)
    assert len(audio_data) > 0

@pytest.mark.parametrize("text,expected_duration", [
    (ENGLISH_TEXT, 2.0),
    ("This is a longer text that should take more time.", 4.0)
])
def test_tts_duration(tts_engine, text, expected_duration):
    """Test TTS output duration."""
    audio_data = tts_engine._synthesize_text(
        text,
        {'speed': 1.0, 'pitch': 0.0, 'energy': 1.0}
    )
    
    duration = len(audio_data) / tts_engine.sample_rate
    assert abs(duration - expected_duration) < 1.0  # Allow 1 second tolerance

def test_translation_pipeline(whisper_recognizer, tts_engine):
    """Test complete translation pipeline."""
    # Test Ukrainian to English
    results = []
    def collect_result(result):
        results.append(result)
    
    whisper_recognizer.set_callback(collect_result)
    whisper_recognizer.set_languages("uk", "en")
    
    # Process some Ukrainian audio
    audio_data = np.zeros(16000)
    whisper_recognizer.process_audio(audio_data)
    
    # Wait for processing
    import time
    time.sleep(2)
    
    assert len(results) > 0
    result = results[0]
    
    # Synthesize the translated text
    audio_output = tts_engine._synthesize_text(
        result['text'],
        {'speed': 1.0, 'pitch': 0.0, 'energy': 1.0}
    )
    
    assert audio_output is not None
    assert len(audio_output) > 0

def test_model_error_handling(whisper_recognizer, tts_engine):
    """Test error handling in AI models."""
    # Test recognition with invalid audio
    result = whisper_recognizer._recognize_audio(np.array([]))
    assert result is None
    
    # Test TTS with empty text
    audio = tts_engine._synthesize_text(
        "",
        {'speed': 1.0, 'pitch': 0.0, 'energy': 1.0}
    )
    assert audio is None

@pytest.mark.gpu
def test_gpu_support(whisper_recognizer, tts_engine):
    """Test GPU support if available."""
    if whisper_recognizer._check_gpu():
        gpu_recognizer = WhisperRecognizer(use_gpu=True)
        assert gpu_recognizer.device == "cuda"
        
        gpu_tts = TTSEngine(use_gpu=True)
        assert gpu_tts.device == "cuda"

def test_performance_metrics(whisper_recognizer):
    """Test performance monitoring."""
    audio_data = np.zeros(16000)
    result = whisper_recognizer._recognize_audio(audio_data)
    
    assert 'processing_time' in result
    assert isinstance(result['processing_time'], float)
    assert result['processing_time'] > 0

def test_language_confidence(whisper_recognizer):
    """Test language detection confidence."""
    audio_data = np.zeros(16000)
    
    # Test Ukrainian
    whisper_recognizer.set_languages("uk", "en")
    confidence = whisper_recognizer.validate_language("Тест", "uk")
    assert confidence > whisper_recognizer.confidence_threshold
    
    # Test Polish
    whisper_recognizer.set_languages("pl", "en")
    confidence = whisper_recognizer.validate_language("Test", "pl")
    assert confidence > whisper_recognizer.confidence_threshold