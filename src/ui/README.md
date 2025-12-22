# UI Module for Real-time Translation System

This directory contains the user interface components for the real-time translation system. The UI provides monitoring and control capabilities for the various services in the translation pipeline.

## Overview

The UI is built using PyQt6 and provides:

1. Service status monitoring and controls
2. Audio device selection
3. Real-time audio level visualization
4. Translation controls and status display
5. System tray integration

## Running the UI

### Prerequisites

- Python 3.8+
- Required packages listed in the main `requirements.txt`
- PipeWire audio system (or PulseAudio)
- Systemd services for the translation pipeline (if running in production mode)

### Installation

The UI is part of the main application. To run it, you need to set up the development environment:

```bash
nix develop
```

### Running the UI

```bash
# Run the main application which includes the UI
nix run

# Or run directly
python -m src.main
```

## Testing

### Unit Tests

Unit tests for the UI components can be run using pytest:

```bash
# Run all UI-related tests
python -m pytest tests/ -k "ui" -v

# Run all tests
python -m pytest tests/
```

### Test Structure

UI tests are organized as follows:

- `tests/test_ui_components.py` - Tests for individual UI components
- `tests/test_service_monitoring.py` - Tests for service status monitoring
- `tests/test_ipc_communication.py` - Tests for IPC communication handling
- `tests/test_integration.py` - End-to-end integration tests

## Architecture

The UI communicates with the backend services through the `TranslationSystem` class, which manages:

1. Audio routing and device management
2. IPC communication with the modular services
3. Real-time statistics collection
4. Status updates and callbacks

The service chain is as follows:
```
Capture Service → Whisper Service → Translation Service → TTS Service → Playback Service
```

## Key Components

### MainWindow
The main UI window that provides:
- Audio device selection
- Service status monitoring
- Start/stop controls for translation
- Audio level visualization
- System tray integration

### Service Status Panel
Shows the status of each service in the pipeline:
- Capture service (audio input)
- Whisper service (speech recognition)
- Translation service (language translation)
- TTS service (text-to-speech)
- Playback service (audio output)

## Configuration

The UI uses the same configuration as the main application, which can be found in the `config/` directory.

## Development

When developing UI features:

1. Make sure to handle IPC communication errors gracefully
2. Update UI state asynchronously to avoid blocking the interface
3. Provide visual feedback for long-running operations
4. Follow the existing code patterns for consistency

## Troubleshooting

### UI Not Responding
- Check if the backend services are running
- Verify that the IPC sockets exist and are accessible
- Check the application logs for errors

### Audio Devices Not Showing
- Ensure PipeWire/PulseAudio is running
- Verify that the virtual audio devices are properly configured
- Check that the application has audio permissions

### Service Status Not Updating
- Verify that the IPC communication is working properly
- Check that services are properly connected to their sockets
- Look for errors in the application logs