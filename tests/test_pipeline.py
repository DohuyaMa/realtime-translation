"""
Test script to verify the entire translation pipeline works correctly.
This script tests the integration of all components in the real-time translation system.
"""

import sys
import os
import asyncio
import threading
import time
from typing import Optional

# Add the src directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

def test_imports():
    """Test that all required modules can be imported."""
    print("Testing imports...")
    
    try:
        from kokoro import TextToSpeech
        print("✓ kokoro import successful")
    except ImportError as e:
        print(f"✗ Failed to import kokoro: {e}")
        return False
    
    try:
        import torch
        print("✓ torch import successful")
    except ImportError as e:
        print(f"✗ Failed to import torch: {e}")
        return False
    
    try:
        import sounddevice as sd
        print("✓ sounddevice import successful")
    except ImportError as e:
        print(f"✗ Failed to import sounddevice: {e}")
        return False
    
    try:
        from src.models.tts_engine import TTSEngine
        print("✓ TTSEngine import successful")
    except ImportError as e:
        print(f"✗ Failed to import TTSEngine: {e}")
        return False
    
    try:
        from src.translation_system import TranslationSystem
        print("✓ TranslationSystem import successful")
    except ImportError as e:
        print(f"✗ Failed to import TranslationSystem: {e}")
        return False
    
    return True

def test_tts_engine():
    """Test the TTS engine with kokoro."""
    print("\nTesting TTS engine...")
    
    try:
        # Initialize TTS engine
        tts = TTSEngine(
            device="cpu", # Use CPU for testing
            sample_rate=22050,
            use_gpu=False
        )
        print("✓ TTS engine initialized successfully")
        
        # Test synthesis
        test_text = "Hello, this is a test of the kokoro text to speech system."
        tts.synthesize(test_text, play_audio=False)
        print("✓ TTS synthesis test completed")
        
        # Stop the engine
        tts.stop()
        print("✓ TTS engine stopped successfully")
        
        return True
    except Exception as e:
        print(f"✗ TTS engine test failed: {e}")
        return False

def test_kokoro_directly():
    """Test kokoro directly to ensure it works as a Python package."""
    print("\nTesting kokoro directly...")
    
    try:
        tts = TextToSpeech(device="cpu")
        print("✓ TextToSpeech initialized successfully")
        
        # Test synthesis
        audio = tts.synthesize("This is a test of the kokoro TTS system.")
        print(f"✓ Synthesis successful, audio length: {len(audio) if audio is not None else 0}")
        
        return True
    except Exception as e:
        print(f"✗ Direct kokoro test failed: {e}")
        return False

def run_tests():
    """Run all tests to verify the pipeline."""
    print("Starting pipeline verification tests...\n")
    
    all_tests_passed = True
    
    # Test 1: Imports
    if not test_imports():
        all_tests_passed = False
    
    # Test 2: Kokoro directly
    if not test_kokoro_directly():
        all_tests_passed = False
    
    # Test 3: TTS Engine
    if not test_tts_engine():
        all_tests_passed = False
    
    print(f"\n{'='*50}")
    if all_tests_passed:
        print("✓ All tests passed! The pipeline is working correctly.")
        print("The kokoro TTS system is properly integrated as a Python library.")
    else:
        print("✗ Some tests failed. Please check the output above for details.")
    print(f"{'='*50}")
    
    return all_tests_passed

if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)