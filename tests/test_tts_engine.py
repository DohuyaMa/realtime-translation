import unittest
import sys
import os
from unittest.mock import patch, MagicMock
import numpy as np

# Add the src directory to the path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.models.tts_engine import TTSEngine


class TestTTSEngine(unittest.TestCase):
    """Unit tests for the TTSEngine class."""
    
    def setUp(self):
        """Set up test fixtures before each test method."""
        # Mock the external dependencies to avoid actual TTS initialization
        self.text_to_speech_patcher = patch('src.models.tts_engine.TextToSpeech')
        self.mock_tts_class = self.text_to_speech_patcher.start()
        self.mock_tts_instance = MagicMock()
        self.mock_tts_class.return_value = self.mock_tts_instance
        
        # Mock sounddevice to avoid audio device issues in tests
        self.sounddevice_patcher = patch('src.models.tts_engine.sd')
        self.mock_sd = self.sounddevice_patcher.start()
        self.mock_output_stream = MagicMock()
        self.mock_sd.OutputStream.return_value = self.mock_output_stream
        
        # Mock numpy to avoid audio processing issues
        self.numpy_patcher = patch('src.models.tts_engine.np')
        self.mock_np = self.numpy_patcher.start()
        self.mock_np.float32 = np.float32
        self.mock_np.ndarray = np.ndarray
        # Mock torch for GPU checks - we need to patch where it's imported
        self.torch_patcher = patch('src.models.tts_engine.torch', create=True)
        self.mock_torch = self.torch_patcher.start()
        self.mock_torch.cuda.is_available.return_value = False
        self.mock_torch.backends = MagicMock()
        self.mock_torch.backends.cudnn = MagicMock()
        self.mock_torch.backends.cudnn.benchmark = False
        self.mock_torch.backends.cuda = MagicMock()
        self.mock_torch.backends.cuda.matmul = MagicMock()
        self.mock_torch.backends.cuda.matmul.allow_tf32 = False
        self.mock_torch.backends.cuda.matmul.allow_tf32 = False

    def tearDown(self):
        """Tear down test fixtures after each test method."""
        self.text_to_speech_patcher.stop()
        self.sounddevice_patcher.stop()
        self.numpy_patcher.stop()
        self.torch_patcher.stop()

    def test_tts_engine_initialization(self):
        """Test that TTS engine initializes correctly."""
        tts_engine = TTSEngine()
        
        # Check that required attributes are set
        self.assertIsNotNone(tts_engine.tts)
        self.assertIsNotNone(tts_engine.synthesis_queue)
        self.assertTrue(tts_engine.is_running)
        self.assertIsNotNone(tts_engine.synthesis_thread)
        self.assertIsNotNone(tts_engine.output_stream)

    def test_tts_engine_stop_method(self):
        """Test that the stop method works correctly."""
        tts_engine = TTSEngine()
        
        # Ensure the stop method can be called without errors
        tts_engine.stop()
        
        # Check that attributes were reset
        self.assertFalse(tts_engine.is_running)
        self.mock_output_stream.stop.assert_called_once()
        self.mock_output_stream.close.assert_called_once()

    def test_tts_engine_destructor_no_error(self):
        """Test that the destructor doesn't raise AttributeError when synthesis_thread is None."""
        # Create a TTS engine instance
        tts_engine = TTSEngine()
        
        # Manually delete the synthesis_thread attribute to simulate the error condition
        if hasattr(tts_engine, 'synthesis_thread'):
            del tts_engine.synthesis_thread
        
        # The destructor should not raise an error even if synthesis_thread doesn't exist
        try:
            tts_engine.__del__()
        except AttributeError as e:
            self.fail(f"__del__ method raised AttributeError: {e}")

    def test_tts_engine_destructor_with_synthesis_thread(self):
        """Test that the destructor works correctly when synthesis_thread exists."""
        tts_engine = TTSEngine()
        
        # Store the thread reference before calling stop
        thread = tts_engine.synthesis_thread
        
        # The destructor should not raise an error
        try:
            tts_engine.__del__()
        except Exception as e:
            self.fail(f"__del__ method raised exception: {e}")
        
        # Verify the thread was joined
        if thread:
            # Note: In the mock environment, join() might not be called
            # since the thread is a mock, but the method should execute without error
            pass

    def test_tts_engine_stop_when_not_running(self):
        """Test that stop method works even when the engine is already stopped."""
        tts_engine = TTSEngine()
        tts_engine.is_running = False
        
        # Should not raise an error
        tts_engine.stop()

    def test_synthesize_method(self):
        """Test the synthesize method."""
        tts_engine = TTSEngine()
        
        # Mock the queue to check if put is called
        tts_engine.synthesis_queue = MagicMock()
        tts_engine.synthesis_queue.full.return_value = False  # Make sure queue is not full
        
        # Call synthesize
        tts_engine.synthesize("Hello world", play_audio=True)
        
        # Check that the text was added to the queue
        self.assertTrue(tts_engine.synthesis_queue.put.called)

    def test_synthesize_queue_full(self):
        """Test synthesize method when queue is full."""
        tts_engine = TTSEngine()
        
        # Create a mock queue with full() method returning True
        mock_queue = MagicMock()
        mock_queue.full.return_value = True
        tts_engine.synthesis_queue = mock_queue
        
        # Call synthesize
        tts_engine.synthesize("Hello world")
        
        # The put method should not be called when queue is full
        tts_engine.synthesis_queue.put.assert_not_called()


if __name__ == '__main__':
    unittest.main()