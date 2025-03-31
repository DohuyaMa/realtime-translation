#!/bin/bash

# Exit on error
set -e

# Directory containing this script
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
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

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    status "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
status "Activating virtual environment..."
source venv/bin/activate

# Install/upgrade dependencies
status "Checking dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

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