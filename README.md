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

## System Requirements

- CPU: 4+ cores
- RAM: 8GB minimum (16GB recommended)
- Storage: 10GB for models
- GPU: Optional, improves model performance
- NixOS with PipeWire/PulseAudio
- Python 3.8+
- ollama
- Docker (optional)

## Quick Start

1. Clone the repository:
   ```bash
   git clone https://github.com/kokoro-org/real-time-translator.git
   cd real-time-translator
   ```

2. Run the application:
   ```bash
   ./run.sh
   ```

The script will automatically:
- Set up virtual environment and dependencies
- Configure virtual audio devices
- Set up Kokoro for voice synthesis
- Create default configuration
- Download required AI models
- Launch the application

## System Architecture

### Components

1. **Audio Processing Layer**
   - PyAudio for microphone capture
   - PipeWire/PulseAudio for virtual devices
   - Real-time audio routing
   - Buffer management (512-4096 samples)
   - Sample rates: 16000-48000 Hz

2. **AI Processing Layer**
   - Whisper AI (via ollama) for speech recognition
   - Local LLM for translation
   - Primary TTS: Kokoro (English synthesis)
   - Backup TTS: Coqui TTS
   - Support for transcription files import

3. **User Interface**
   - Qt-based desktop application
   - Audio visualization
   - Device configuration
   - Language selection
   - Profile management

## Dependencies

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

## Configuration

The application automatically creates a default configuration during first run. You can customize the following settings:

### AI Model Configuration

1. Language Models
   - Speech recognition: Whisper (small, medium, or large)
   - Translation: Choose appropriate ollama model
   - TTS: Select Kokoro (English) or Coqui TTS (other languages)

2. Performance Settings
   - Buffer size: 512-4096 samples (lower = less latency, higher = more stable)
   - Model inference optimization
   - CPU/GPU utilization

### Profile Management

Create profiles for different use cases:
- Meeting profile (optimized for voice)
- Presentation profile (balanced quality)
- High-quality profile (maximum accuracy)

## Development

### Setup Development Environment

1. Install development dependencies:
   ```bash
   pip install -r requirements-dev.txt
   ```

2. Install pre-commit hooks:
   ```bash
   pip install pre-commit
   pre-commit install
   ```

### Running Tests

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_audio.py

# Run with coverage
pytest --cov=src tests/
```

## Documentation

Comprehensive documentation is available in the `docs/` directory:

- [Installation Guide](docs/INSTALLATION.md) - Detailed setup instructions
- [Technical Documentation](docs/TECHNICAL.md) - System architecture and components
- [Configuration Guide](docs/CONFIGURATION.md) - Detailed config options
- [Languages Support](docs/LANGUAGES.md) - Supported languages and models
- [UI Guide](docs/UI_GUIDE.md) - User interface documentation
- [Kokoro Integration](docs/KOKORO_INTEGRATION.md) - Voice synthesis setup

## Usage

After running `./run.sh`, the application will start automatically. Then:

1. Select input/output devices
2. Choose source and target languages
3. Configure audio routing
4. Start translation

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
   - Check PipeWire/PulseAudio configuration: `pactl list sinks`
   - Verify virtual device creation: `pactl list sources`
   - Check application permissions
   - Monitor audio levels

2. AI Model Issues
   - Verify ollama is running: `ollama list`
   - Check model downloads in `models/` directory
   - Monitor system resources (CPU/RAM usage)
   - Verify GPU acceleration if enabled

3. Performance Issues
   - Adjust buffer sizes (512-4096)
   - Check CPU/GPU usage
   - Optimize model settings
   - Monitor latency with `./scripts/system_check.sh`

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

This project is licensed under the MIT License.

Copyright (c) 2024 Kokoro Organization

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.