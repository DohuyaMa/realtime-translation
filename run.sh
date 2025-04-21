#!/usr/bin/env bash

# Exit on error
set -e

# Directory containing this script
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print status messages
status() {
    echo -e "${GREEN}[*]${NC} $1"
}

error() {
    echo -e "${RED}[!]${NC} $1"
}

warning() {
    echo -e "${YELLOW}[!]${NC} $1"
}

info() {
    echo -e "${BLUE}[i]${NC} $1"
}

# Function to detect first run
is_first_run() {
    [ ! -f "$HOME/.config/real-time-translator/config.yml" ] || \
    [ ! -f "$HOME/.config/pipewire/virtual-devices.conf" ] || \
    [ ! -d "$HOME/.local/share/real-time-translator/models/kokoro" ]
}

# Function to set up virtual audio devices
setup_virtual_devices() {
    status "Setting up virtual audio devices..."
    
    # Create config directory
    mkdir -p ~/.config/pipewire
    
    # Create virtual devices configuration
    cat << EOF > ~/.config/pipewire/virtual-devices.conf
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
    sleep 2 # Wait for PipeWire to restart
}

# Function to create default configuration
setup_default_config() {
    status "Creating default configuration..."
    local config_dir="$HOME/.config/real-time-translator"
    mkdir -p "$config_dir"
    
    cat << EOF > "$config_dir/config.yml"
app:
  name: "Real-Time Translator"
  language: "en"
  theme: "system"

audio:
  input_device: "default"
  output_device: "virtual_output"
  sample_rate: 16000
  buffer_size: 1024
  processing:
    noise_reduction: true
    echo_cancellation: true
    auto_gain: true

translation:
  source_language: "auto"
  target_language: "en"
  model: "whisper-medium"
  
tts:
  engine: "kokoro"
  fallback: "coqui"
  speed: 1.0
  pitch: 1.0

performance:
  gpu_enabled: true
  threads: 4
  batch_size: 16
EOF
}

# Function to setup Kokoro
setup_kokoro() {
    status "Setting up Kokoro..."
    python3 scripts/setup_kokoro.py
}

# Check Python version
if ! command -v python3 &> /dev/null; then
    error "Python 3 is not installed"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
if (( $(echo "$PYTHON_VERSION < 3.8" | bc -l) )); then
    error "Python 3.8 or higher is required (found $PYTHON_VERSION)"
    exit 1
fi

# Check if this is first run
if is_first_run; then
    info "First time setup detected. Running initial configuration..."
    
    # Create virtual environment if it doesn't exist
    if [ ! -d "venv" ]; then
        status "Creating virtual environment..."
        python3 -m venv venv
    fi
    
    # Activate virtual environment
    status "Activating virtual environment..."
    source venv/bin/activate
    
    # Install/upgrade dependencies
    status "Installing dependencies..."
    pip install --upgrade pip
    pip install -r requirements.txt
    
    # Set up virtual audio devices
    if ! pactl info &> /dev/null; then
        error "PulseAudio/PipeWire is not running"
        exit 1
    fi
    setup_virtual_devices
    
    # Create default configuration
    setup_default_config
    
    # Setup Kokoro
    setup_kokoro
else
    # Normal startup
    if [ ! -d "venv" ]; then
        status "Creating virtual environment..."
        python3 -m venv venv
    fi
    
    status "Activating virtual environment..."
    source venv/bin/activate
    
    status "Checking dependencies..."
    pip install --upgrade pip
    pip install -r requirements.txt
fi

# Check if ollama is installed and running
if ! command -v ollama &> /dev/null; then
    warning "ollama is not installed. Please install it from: https://ollama.ai/"
    exit 1
fi

if ! pgrep ollama > /dev/null; then
    warning "ollama is not running. Starting ollama..."
    systemctl --user start ollama || {
        error "Failed to start ollama"
        exit 1
    }
fi

# Check for required models
status "Checking required models..."
if ! ollama list | grep -q "whisper"; then
    status "Downloading Whisper model..."
    ollama pull whisper
fi

if ! ollama list | grep -q "mistral"; then
    status "Downloading Mistral model..."
    ollama pull mistral
fi

# Create necessary directories
mkdir -p ~/.config/real-time-translator
mkdir -p ~/.local/share/real-time-translator/logs

# Check audio setup
status "Checking audio setup..."
if ! pactl info &> /dev/null; then
    error "PulseAudio/PipeWire is not running"
    exit 1
fi

# Export Python path
export PYTHONPATH="$SCRIPT_DIR:$PYTHONPATH"

# Start the application
status "Starting Real-Time Translator..."
python3 -m src.main "$@"