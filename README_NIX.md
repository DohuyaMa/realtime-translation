# Real-time Speech Translation System - Nix Setup

This document explains the dependencies and setup process using the Nix package manager.

## Dependencies Analysis

### Python Dependencies
- **PySide6**: GUI framework for the application interface
- **pyaudio**: Audio input/output handling
- **numpy**: Numerical operations for audio processing
- **sounddevice**: Cross-platform audio I/O
- **torch**: PyTorch for machine learning models
- **transformers**: Hugging Face transformers library
- **whisper**: OpenAI Whisper for speech recognition
- **onnxruntime**: ONNX runtime for model execution
- **soundfile**: Audio file reading/writing
- **librosa**: Audio signal processing
- **pipewire-python**: PipeWire audio system integration
- **pulsectl**: PulseAudio control library
- **PyYAML**: YAML configuration file parsing
- **python-dotenv**: Environment variable management
- **loguru**: Logging library
- **kokoro**: TTS engine (from nixpkgs, using KPipeline)

### System Dependencies
- **libffi**: Foreign Function Interface library
- **openssl**: SSL/TLS cryptographic library
- **zlib**: Compression library
- **gcc**: GNU Compiler Collection for building extensions
- **gnumake**: Build automation tool
- **pkg-config**: Compiler configuration tool

### Runtime Dependencies
- **Pipewire/PulseAudio**: Audio subsystem for Linux
- **Ollama**: Local LLM service for translation
- **Docker/Docker Compose**: Containerized services (optional)

## Nix Setup

### Prerequisites
- Nix package manager installed
- Nix Flakes enabled

### Development Environment
To enter the development environment:
```bash
nix develop
```

This will provide:
- Python 3.13 with all required packages including kokoro TTS
- System tools (git, docker, nodejs, etc.)
- Audio subsystem tools
- AI/ML tools (ollama)

### Building the Package
To build the application:
```bash
nix build
```

### Running the Application
To run the application:
```bash
nix run
```

Or with development environment:
```bash
nix develop
just run
```

## Project Commands

The project uses `just` as a command runner. Available commands:

- `just setup`: Setup the development environment
- `just run`: Run the application
- `just run-debug`: Run with debug logging
- `just test`: Run tests
- `just ollama-start`: Start Ollama service
- `just ollama-pull-models`: Download required models
- `just audio-setup`: Setup virtual audio devices
- `just logs`: View application logs

## Audio Configuration

The application uses virtual audio devices through PipeWire. The setup creates:
- `virtual_input`: Virtual input device for audio routing
- `virtual_output`: Virtual output device for audio routing

These are configured in `~/.config/pipewire/virtual-devices.conf`.

## Ollama Integration

The application requires Ollama with the following models:
- `whisper`: For speech recognition
- `mistral`: For translation

These can be downloaded with:
```bash
just ollama-pull-models
```

## Development Notes

The Nix setup uses a modular approach with flake-parts to handle the build configuration. The main flake.nix file imports from the modular structure in `flake-global/`. This approach separates concerns and makes the configuration more maintainable.

The flake.nix file defines:
- A Python environment with all required packages
- System dependencies needed for audio processing
- Development tools for building and testing
- Runtime services needed by the application

The modular structure includes:
- `flake-global/prod/`: Production-specific configurations
- `flake-global/dev/`: Development-specific configurations
- `flake-global/home-manager-module.nix`: Home Manager module for the application

The development shell provides all necessary tools for development, including Python packages, system libraries, and development utilities.