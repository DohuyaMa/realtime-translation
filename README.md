# Real-Time Voice Translation System

A desktop application for real-time voice translation with integration capabilities for communication platforms like Teams and Zoom.

## Features

- Real-time speech recognition and translation
- Support for multiple languages
- Integration with communication platforms
- Virtual audio device routing
- Profile management for different use cases
- Audio quality monitoring
- Local AI model processing

## System Architecture

### Components

1. **Audio Processing Layer**
   - PyAudio for microphone capture
   - PipeWire/PulseAudio for virtual devices
   - Real-time audio routing

2. **AI Processing Layer**
   - Whisper AI (via ollama) for speech recognition
   - Local LLM for translation
   - Coqui TTS for voice synthesis

3. **User Interface**
   - Qt-based desktop application
   - Audio visualization
   - Device configuration
   - Language selection
   - Profile management

## Installation

### Prerequisites

- NixOS with PipeWire/PulseAudio
- Python 3.8+
- ollama
- Docker (optional)

### Dependencies

```nix
# configuration.nix
{
  # Enable sound with pipewire
  sound.enable = true;
  security.rtkit.enable = true;
  services.pipewire = {
    enable = true;
    alsa.enable = true;
    alsa.support32Bit = true;
    pulse.enable = true;
  };

  # Python and Qt dependencies
  environment.systemPackages = with pkgs; [
    python3
    python3Packages.pyqt5
    python3Packages.pyaudio
    python3Packages.torch
    ollama
  ];
}
```

### Installation Steps

1. Clone the repository:
   ```bash
   git clone [repository-url]
   cd real-time-translator
   ```

2. Create and activate virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate
   ```

3. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Download required AI models:
   ```bash
   ./scripts/setup_models.sh
   ```

## Configuration

### Audio Setup

1. Configure virtual audio devices:
   ```bash
   # Create virtual input device
   pactl load-module module-null-sink sink_name=virtual_input
   
   # Create virtual output device
   pactl load-module module-null-sink sink_name=virtual_output
   ```

2. Configure default devices in the application settings

### AI Model Configuration

1. Language Models
   - Speech recognition: Whisper (small, medium, or large)
   - Translation: Choose appropriate ollama model
   - TTS: Select Coqui TTS model

2. Performance Settings
   - Buffer size for audio processing
   - Model inference optimization
   - CPU/GPU utilization

### Profile Management

Create profiles for different use cases:
- Meeting profile (optimized for voice)
- Presentation profile (balanced quality)
- High-quality profile (maximum accuracy)

## Usage

1. Launch the application:
   ```bash
   ./run.sh
   ```

2. Select input/output devices
3. Choose source and target languages
4. Configure audio routing
5. Start translation

### Integration with Communication Apps

#### Teams
1. Set virtual output as Teams microphone input
2. Adjust audio levels in Teams settings

#### Zoom
1. Select virtual output device as microphone
2. Test audio in Zoom settings

## Troubleshooting

### Common Issues

1. Audio Routing Problems
   - Check PipeWire/PulseAudio configuration
   - Verify virtual device creation
   - Check application permissions

2. AI Model Issues
   - Verify ollama is running
   - Check model downloads
   - Monitor system resources

3. Performance Issues
   - Adjust buffer sizes
   - Check CPU/GPU usage
   - Optimize model settings

## Customization

### Adding New Languages
1. Download language models
2. Update configuration
3. Test translation quality

### Custom Voice Models
1. Train custom TTS models
2. Import into the system
3. Configure voice settings

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development guidelines.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.