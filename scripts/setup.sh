#!/bin/bash

# Exit on error
set -e

# Directory containing this script
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Function to print status messages
status() {
    echo -e "${GREEN}[*]${NC} $1"
}

warning() {
    echo -e "${YELLOW}[!]${NC} $1"
}

error() {
    echo -e "${RED}[!]${NC} $1"
}

# Check if running on NixOS
if [ -e /etc/nixos ]; then
    status "Running on NixOS"
    
    # Check for required packages
    if ! command -v python3 &> /dev/null; then
        error "Python 3 is not installed"
        exit 1
    fi
    
    # Check PipeWire
    if ! systemctl --user is-active --quiet pipewire; then
        warning "PipeWire is not running"
        status "Starting PipeWire..."
        systemctl --user start pipewire pipewire-pulse
    fi
else
    warning "Not running on NixOS, some features might not work correctly"
fi

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    status "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
status "Activating virtual environment..."
source venv/bin/activate

# Install requirements
status "Installing requirements..."
pip install --upgrade pip
pip install -r requirements.txt

# Make setup script executable
chmod +x "$SCRIPT_DIR/setup_kokoro.py"

# Run Kokoro setup
status "Running Kokoro setup..."
"$SCRIPT_DIR/setup_kokoro.py"

# Deactivate virtual environment
deactivate

status "Setup completed successfully!"
warning "Don't forget to activate the virtual environment: source venv/bin/activate"