# Real-time Translator Project Commands

# Default recipe - show available commands
default:
    just --list

# Setup the development environment
setup:
    # Install Python dependencies
    pip install --upgrade pip
    pip install -r requirements.txt
    
    # Setup Kokoro TTS
    python scripts/setup_kokoro.py

# Run the application
run:
    python -m src.main {{@}}

# Run with debug logging
run-debug:
    python -m src.main --debug {{@}}

# Run tests
test:
    python -m pytest tests/

# Run specific test
test-run:
    python -m pytest {{test_path}} -v

# Start Ollama service
ollama-start:
    systemctl --user start ollama

# Check Ollama status
ollama-status:
    systemctl --user status ollama

# Pull required Ollama models
ollama-pull-models:
    ollama pull whisper
    ollama pull mistral

# Start Docker services
docker-up:
    docker-compose up -d

# Stop Docker services
docker-down:
    docker-compose down

# Check audio setup
audio-check:
    pactl info
    pactl list sinks short
    pactl list sources short

# Setup virtual audio devices
audio-setup:
    # Create PipeWire virtual devices configuration
    mkdir -p ~/.config/pipewire
    cat > ~/.config/pipewire/virtual-devices.conf << 'EOF'
context.modules = [
    {   name = module-null-sink
        args = {
            sink_name = "virtual_input"
            audio.position = [ FL FR ]
        }
    }
    {   name = module-null-sink
        args = {
            sink_name = "virtual_output"
            audio.position = [ FL FR ]
        }
    }
]
EOF
    # Restart PipeWire to apply changes
    systemctl --user restart pipewire
    sleep 2

# Clean logs
clean-logs:
    rm -rf ~/.local/share/real-time-translator/logs/*

# Show application logs
logs:
    tail -f ~/.local/share/real-time-translator/logs/app.log

# Create virtual environment
venv-create:
    python3 -m venv venv
    source venv/bin/activate && pip install --upgrade pip

# Activate virtual environment
venv-activate:
    source venv/bin/activate

# Format Python code
format:
    black src/ tests/ scripts/
    isort src/ tests/ scripts/

# Check code style
lint:
    flake8 src/ tests/ scripts/
    mypy src/