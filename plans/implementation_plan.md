[test.iteration.md](context/test.iteration.md)

# Step-by-Step Implementation Plan

This document outlines the specific fixes needed to resolve the issues described in the test.iteration.md file.

## Issue 1: Kokoro/Kokoro_ONNX Installation Error (Critical)

### Location

- `src/models/tts_engine.py` - TTS engine initialization
- `tests/test_ai_models.py` - TTS-related tests fail during setup

### Root Cause

The kokoro TTS library is not available in the Nix environment, causing import failures in the TTS engine.

### Solution Steps

1. **Modify flake.nix** to include the kokoro package in the development environment
2. **Update TTS engine** to handle cases when kokoro is not available
3. **Create a mock TTS engine** for testing purposes when kokoro is not available

### Files to Modify

1. `flake.nix` - Add kokoro package
2. `src/models/tts_engine.py` - Add import fallbacks and mock functionality
3. `tests/test_ai_models.py` - Add mock TTS engine for testing

## Issue 2: Whisper Recognition Command Error (Major)

### Location

- `src/models/whisper_recognition.py` - All Whisper tests fail
- `tests/test_ai_models.py` - All Whisper tests fail

### Root Cause

The WhisperRecognizer is using ollama to run Whisper, but the command-line arguments format is incorrect or the Whisper model in ollama doesn't support the `--language` flag as expected.

### Solution Steps

1. **Fix command construction** in `src/models/whisper_recognition.py`
2. **Add proper error handling** for unsupported flags
3. **Update the model initialization** to use correct arguments

### Files to Modify

1. `src/models/whisper_recognition.py` - Fix command construction and error handling
2. `tests/test_ai_models.py` - Update tests to handle new API

## Issue 3: Audio Processor Speech Detection Failure (Medium)

### Location

- `tests/test_audio.py::test_audio_processor_speech_detection`
- `src/audio/processor.py` - Audio processor implementation

### Root Cause

The test generates a sine wave as "speech" but the audio processor's speech detection algorithm doesn't recognize it as speech.

### Solution Steps

1. **Improve test data** to use more realistic audio that would trigger speech detection
2. **Adjust speech detection thresholds** in the audio processor
3. **Mock the speech detection callback** in tests for more predictable results

### Files to Modify

1. `tests/test_audio.py` - Update test data and improve speech detection test
2. `src/audio/processor.py` - Adjust speech detection thresholds and algorithm

## Issue 4: Hanging Tests (Medium)

### Location

- `tests/test_ipc_communication.py::test_ipc_server_unknown_message_type` hangs
- `tests/test_audio.py::test_audio_pipeline` hangs
- `tests/test_audio.py::test_audio_levels` hangs

### Root Cause

These tests likely create threads or processes that don't properly terminate, causing infinite loops or blocking operations.

### Solution Steps

1. **Add proper timeouts** to all blocking operations
2. **Implement proper cleanup** in test fixtures
3. **Use pytest-timeout** plugin to prevent hanging tests

### Files to Modify

1. `tests/conftest.py` - Add multiprocessing spawn method and timeout configuration
2. `tests/test_ipc_communication.py` - Add proper cleanup and timeouts
3. `tests/test_audio.py` - Add proper cleanup and timeouts

## Implementation Order

1. **First**: Update `flake.nix` to include kokoro package
2. **Second**: Fix TTS engine with proper fallbacks
3. **Third**: Fix Whisper recognition command construction
4. **Fourth**: Update audio processor and tests
5. **Fifth**: Fix hanging tests with proper cleanup and timeouts
6. **Sixth**: Update test configuration files

## Additional Configuration Files to Update

1. `pytest.ini` or `setup.cfg` - Add pytest-timeout configuration
2. `requirements.txt` - Add any missing dependencies for testing
