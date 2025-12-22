# Running and Testing the Real-time Translation System

This document explains how to run and test the real-time translation system with the new systemd service configuration.

## Prerequisites

- Nix package manager installed
- PipeWire audio system (recommended) or PulseAudio
- Microphone and speakers for audio input/output

## Setup

### 1. Enter the Development Environment

```bash
nix develop
```

Or using the flake directly:

```bash
nix develop github:your-username/real-time-translator
```

### 2. Install PipeWire Virtual Sinks

Before running the services, you need to set up virtual audio sinks:

```bash
python install_pipewire_config.py
# Or manually restart PipeWire:
systemctl --user restart pipewire pipewire-pulse
```

## Running the Services

### Method 1: Using Systemd Services (Recommended)

The system provides several systemd user services that can be managed independently:

#### Start the virtual sinks service:
```bash
systemctl --user start rt-virtual-sinks
systemctl --user enable rt-virtual-sinks  # To start automatically
```

#### Start individual services:
```bash
# Start the capture service (listens for audio)
systemctl --user start rt-capture

# Start the whisper service (speech recognition)
systemctl --user start rt-whisper

# Start the translation service
systemctl --user start rt-translate

# Start the TTS service (text-to-speech)
systemctl --user start rt-tts

# Start the playback service (audio output)
systemctl --user start rt-playback
```

#### Or start all services at once:
```bash
systemctl --user start rt-capture rt-whisper rt-translate rt-tts rt-playback
systemctl --user enable rt-capture rt-whisper rt-translate rt-tts rt-playback  # To start automatically
```

#### Check service status:
```bash
systemctl --user status rt-capture
systemctl --user status rt-whisper
# etc.
```

#### View service logs:
```bash
journalctl --user -u rt-capture -f
journalctl --user -u rt-whisper -f
# etc.
```

### Method 2: Running Directly

You can also run the services directly without systemd:

```bash
# Run the main application
nix run

# Or run individual services directly:
python -m src.capture.capture_service --socket-path /tmp/rt-capture.sock
python -m src.whisper.whisper_service --socket-path /tmp/rt-whisper.sock
python -m src.translate.translate_service --socket-path /tmp/rt-translate.sock
python -m src.tts.tts_service --socket-path /tmp/rt-tts.sock
python -m src.playback.playback_service --socket-path /tmp/rt-playback.sock
```

## Testing the System

### 1. Basic Functionality Test

1. Start all services:
   ```bash
   systemctl --user start rt-virtual-sinks rt-capture rt-whisper rt-translate rt-tts rt-playback
   ```

2. Speak into your microphone. The system should:
   - Capture audio via the rt-capture service
   - Recognize speech via the rt-whisper service
   - Translate the recognized text via the rt-translate service
   - Convert the translation to speech via the rt-tts service
   - Play the translated speech via the rt-playback service

### 2. Individual Service Testing

#### Test Audio Capture:
```bash
# Check if the capture service is running
systemctl --user status rt-capture

# Check logs for audio activity
journalctl --user -u rt-capture -f
```

#### Test Speech Recognition:
```bash
# Test with a sample audio file (if available)
# The whisper service should recognize speech and return text
```

#### Test Translation:
```bash
# The translation service should translate text from source to target language
# Check logs for translation activity
journalctl --user -u rt-translate -f
```

#### Test TTS:
```bash
# The TTS service should convert text to speech
# Check logs for synthesis activity
journalctl --user -u rt-tts -f
```

#### Test Playback:
```bash
# The playback service should output audio through the virtual sink
# You can monitor the virtual output sink to hear the results
```

### 3. Socket Communication Testing

Each service communicates via UNIX sockets. You can test these directly:

```bash
# The services create sockets in the temporary directory
ls -la /tmp/rt-*.sock
# Or in the user runtime directory:
ls -la $XDG_RUNTIME_DIR/rt-*.sock
```

## Troubleshooting

### Common Issues

#### 1. Audio Not Working
- Ensure PipeWire/PulseAudio is running
- Check that virtual sinks are created:
  ```bash
  pactl list sinks short
  pactl list sources short
  ```
- Make sure the virtual sinks `rt_virtual_input` and `rt_virtual_output` exist

#### 2. Services Failing to Start
- Check service logs:
  ```bash
  journalctl --user -u rt-capture -f
  ```
- Ensure all dependencies are installed (the Nix environment should handle this)

#### 3. Permission Issues
- Make sure socket files have correct permissions
- Check that the user has audio group access

### Useful Commands

#### List all related services:
```bash
systemctl --user list-units --type=service | grep rt-
```

#### List all related sockets:
```bash
systemctl --user list-sockets | grep rt-
```

#### Restart all services:
```bash
systemctl --user restart rt-virtual-sinks rt-capture rt-whisper rt-translate rt-tts rt-playback
```

#### Stop all services:
```bash
systemctl --user stop rt-virtual-sinks rt-capture rt-whisper rt-translate rt-tts rt-playback
```

## Architecture Overview

The system consists of these main services:

1. **rt-capture**: Captures audio from the microphone and sends it via IPC
2. **rt-whisper**: Performs speech recognition using Whisper models
3. **rt-translate**: Translates text between languages using transformer models
4. **rt-tts**: Converts translated text back to speech using TTS
5. **rt-playback**: Plays the final translated audio
6. **rt-virtual-sinks**: Creates virtual audio sinks for routing

All services communicate through UNIX sockets for efficient inter-process communication.

## UI Module

### Running the UI

The UI provides a graphical interface to monitor and control the translation services:

```bash
# Run the full application with UI
nix run

# Or run directly
python -m src.main
```

### UI Features

The UI includes the following features:

1. **Service Status Monitoring**: Real-time status indicators for each service (capture, whisper, translate, TTS, playback)
2. **Individual Service Controls**: Start/stop buttons for each service in the pipeline
3. **Audio Device Selection**: Dropdown menus to select input and output audio devices
4. **Real-time Audio Level Visualization**: Progress bars showing input and output audio levels
5. **Language Selection**: Dropdown menus to select source and target languages
6. **Translation Toggle**: Button to start/stop the entire translation process
7. **System Tray Integration**: Minimize to system tray with quick access to controls

### UI Testing

To run UI-specific tests:

```bash
# Run all UI tests
python -m pytest tests/test_ui_components.py -v

# Run service status tests
python -m pytest tests/test_service_status.py -v

# Run IPC communication tests
python -m pytest tests/test_ipc_communication.py -v

# Run integration tests that include UI
python -m pytest tests/test_integration.py -v
```

### UI Development

When developing UI features:

1. Make sure to handle IPC communication errors gracefully
2. Update UI state asynchronously to avoid blocking the interface
3. Provide visual feedback for long-running operations
4. Test with various audio device configurations
5. Verify service status indicators update correctly