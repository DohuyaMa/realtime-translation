# Real-time Speech Translation System

A modular real-time speech translation system that captures audio from a microphone, performs speech recognition, translates the text, and synthesizes the translated text to speech - all in real-time.

## Table of Contents
- [Architecture](#architecture)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Services](#services)
- [Systemd Integration](#systemd-integration)
- [Nix Flake Structure](#nix-flake-structure)
- [Troubleshooting](#troubleshooting)

## Architecture

The system follows a modular architecture with separate services communicating via UNIX sockets:

```
[Physical Mic] → [Capture Service] → [Whisper Service] → [Translation Service] → [TTS Service] → [Playback Service] → [rt_virtual_output]
                                                                                                                        ↓
                                                                                                        [rt_virtual_output.monitor] → [Teams/Zoom]
```

### Components
- **Capture Service**: Handles audio input from the microphone
- **Whisper Service**: Performs speech recognition using OpenAI Whisper
- **Translation Service**: Translates text between languages
- **TTS Service**: Synthesizes text to speech
- **Playback Service**: Handles audio output to the virtual microphone
### Fixed Device Names

The system uses these fixed device names instead of creating virtual devices dynamically:
```python
VIRTUAL_INPUT_SINK = "rt_virtual_input"
VIRTUAL_OUTPUT_SINK = "rt_virtual_output"
VIRTUAL_MIC_SOURCE = "rt_virtual_output.monitor"
```

## Installation

### Prerequisites
- Nix package manager
- PipeWire audio server
- systemd (for automatic virtual sink creation)

### Setup

1. **Clone the repository:**
   ```bash
   git clone <repository-url>
   cd real-time-translator
   ```

2. **Enter the Nix development environment:**
   ```bash
   nix develop
   ```

3. **Set up virtual microphones using systemd service (recommended):**
   ```bash
   # Run the installation script to set up the systemd service
   python install_pipewire_config.py
   ```
   
   Or manually:
   ```bash
   # Copy the systemd service file
   mkdir -p ~/.config/systemd/user
   cp systemd/rt-virtual-sinks.service ~/.config/systemd/user/
   
   # Reload systemd daemon
   systemctl --user daemon-reload
   
   # Enable and start the service
   systemctl --user enable --now rt-virtual-sinks.service
   ```

4. **Verify PipeWire nodes:**
   ```bash
   pactl list sinks short | grep rt_
   pactl list sources short | grep rt_
   ```
   
   You should see:
   - `rt_virtual_input`
   - `rt_virtual_output`
   - `rt_virtual_output.monitor`

## Configuration

### PipeWire Configuration

The system uses a systemd service to create virtual devices automatically after PipeWire starts:
- `rt_virtual_input` - sink where Python writes sound
- `rt_virtual_output` - sink for Teams/Zoom to use as mic
- `rt_virtual_output.monitor` - the actual microphone that Teams/Zoom sees

The virtual sinks are created using pactl commands in the systemd service, which is the reliable approach on NixOS and modern PipeWire systems.

### Fixed Device Names

The system uses these fixed device names instead of creating virtual devices dynamically:
```python
VIRTUAL_INPUT_SINK = "rt_virtual_input"
VIRTUAL_OUTPUT_SINK = "rt_virtual_output"
VIRTUAL_MIC_SOURCE = "rt_virtual_output.monitor"

## Usage

### Starting the Services

The system can be run using systemd socket activation:

1. **Copy systemd service files:**
   ```bash
   mkdir -p ~/.config/systemd/user
   cp systemd/*.socket systemd/*.service ~/.config/systemd/user/
   ```

2. **Enable and start socket activation:**
   ```bash
   systemctl --user enable rt-capture.socket
   systemctl --user enable rt-whisper.socket
   systemctl --user enable rt-translate.socket
   systemctl --user enable rt-tts.socket
   systemctl --user enable rt-playback.socket

   systemctl --user start rt-capture.socket
   systemctl --user start rt-whisper.socket
   systemctl --user start rt-translate.socket
   systemctl --user start rt-tts.socket
   systemctl --user start rt-playback.socket
   ```

### Manual Usage

Alternatively, you can run services manually:

```bash
# In separate terminals, run each service:
python -m src.capture.capture_service
python -m src.whisper.whisper_service
python -m src.translate.translate_service
python -m src.tts.tts_service
python -m src.playback.playback_service
```

### Teams/Zoom Configuration

In Teams or Zoom audio settings:
- Set microphone to "RT Virtual Output (Microphone)" (which is `rt_virtual_output.monitor`)

## Services

### Capture Service
- Path: `src/capture/capture_service.py`
- Socket: `/tmp/rt-capture.sock`
- Handles audio input from the microphone
- Supports start/stop capture and status queries

### Whisper Service
- Path: `src/whisper/whisper_service.py`
- Socket: `/tmp/rt-whisper.sock`
- Performs speech recognition
- Supports language setting and processing

### Translation Service
- Path: `src/translate/translate_service.py`
- Socket: `/tmp/rt-translate.sock`
- Translates text between languages
- Supports language setting and translation

### TTS Service
- Path: `src/tts/tts_service.py`
- Socket: `/tmp/rt-tts.sock`
- Synthesizes text to speech
- Supports text synthesis

### Playback Service
- Path: `src/playback/playback_service.py`
- Socket: `/tmp/rt-playback.sock`
- Handles audio output to the virtual microphone
- Supports audio playback and device setting

## Systemd Integration

The system includes systemd socket activation for efficient resource management:

## Nix Flake Structure

The project uses a modular Nix flake configuration to separate concerns and improve maintainability. The main flake.nix file imports from a modular structure in the `flake-global/` directory.

### Directory Structure

```
flake-global/
├── flake.nix              # Main flake using flake-parts
├── flake-parts.nix        # Flake-parts configuration framework
├── home-manager-module.nix # Home Manager module for the application
├── prod/                  # Production-specific configurations
│   ├── packages.nix       # Production packages
│   └── apps.nix           # Application definitions
└── dev/                   # Development-specific configurations
    └── devshell.nix       # Development shell environment
```

### Components

- **Production configurations** (`flake-global/prod/`): Contains packages and apps definitions optimized for production use
- **Development configurations** (`flake-global/dev/`): Defines the development shell with all necessary tools and dependencies
- **Home Manager module** (`flake-global/home-manager-module.nix`): Provides integration with Home Manager for user environment setup
- **NixOS module**: Available in `nixosModules/virtual-sinks.nix` for system-level PipeWire virtual sink configuration

This modular approach allows for better separation of development and production environments while maintaining a consistent interface through the main flake.nix file.

### Socket Files
- `rt-capture.socket` - Capture service socket
- `rt-whisper.socket` - Whisper service socket
- `rt-translate.socket` - Translation service socket
- `rt-tts.socket` - TTS service socket
- `rt-playback.socket` - Playback service socket

### Service Files
- `rt-capture.service` - Capture service
- `rt-whisper.service` - Whisper service
- `rt-translate.service` - Translation service
- `rt-tts.service` - TTS service
- `rt-playback.service` - Playback service

Benefits:
- Services start only when there's incoming data
- Automatic restart on failure
- Clean shutdown handling

## Troubleshooting

### Service Status
Check service status with:
```bash
systemctl --user status rt-*.service
systemctl --user status rt-*.socket
```

### Logs
View service logs with:
```bash
journalctl --user -u rt-*.service -f
```

### Socket Status
Check socket activation with:
```bash
systemctl --user list-sockets | grep rt-
```

### PipeWire Issues
If virtual devices don't appear:
```bash
systemctl --user restart pipewire pipewire-pulse
pactl list sinks short
pactl list sources short
```

### Audio Issues
- Verify that the correct input device is selected in your application
- Check that the virtual microphone is selected in Teams/Zoom
- Ensure PipeWire is running and the configuration file is in place

## Development

### Adding New Features
- Each service is modular and can be developed independently
- Follow the IPC protocol when adding new message types
- Use the base IPC classes for consistent communication

### Testing Individual Services
Each service can be tested independently by connecting to its socket and sending appropriate messages.

## Benefits of Modular Architecture

1. **Modularity**: Each component runs as a separate process
2. **Resilience**: Failure in one service doesn't crash the entire pipeline
3. **Resource Efficiency**: Socket activation means services only run when needed
4. **Maintainability**: Easier to debug individual components
5. **Scalability**: Services can be optimized independently
6. **Flexibility**: Services can be updated independently

## IPC Protocol

### Message Format
```
[4-byte length][JSON message]
```

### Message Structure
```json
{
  "type": "message_type",
  "data": { ... }
}
```

### Common Message Types

#### Capture Service
- `start_capture` - Start audio capture
- `stop_capture` - Stop audio capture
- `get_status` - Get service status

#### Whisper Service
- `process_audio` - Process audio for speech recognition
- `get_status` - Get service status
- `set_languages` - Set source/target languages

#### Translation Service
- `translate_text` - Translate text
- `get_status` - Get service status
- `set_languages` - Set source/target languages

#### TTS Service
- `synthesize_text` - Synthesize text to speech
- `get_status` - Get service status

#### Playback Service
- `play_audio` - Play audio data
- `get_status` - Get service status
- `set_device` - Set output device