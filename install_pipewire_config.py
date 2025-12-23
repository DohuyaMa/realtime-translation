#!/usr/bin/env python3
"""Script to inform users about the PipeWire virtual sinks configuration for the real-time translation system."""

import os
import sys
import subprocess
from pathlib import Path


def install_pipewire_config():
    """Inform the user that virtual sinks are configured via Home Manager through the flake."""
    print("Virtual sinks configuration for Real-Time Translator")
    print("=" * 50)
    print("The virtual sinks are configured via Home Manager through the flake.nix")
    print("No systemd service installation is needed anymore.")
    print("")
    print("Virtual devices:")
    print("  - rt_virtual_input (sink where Python writes sound)")
    print("  - rt_virtual_output (sink for Teams/Zoom to use as mic)")
    print("  - rt_virtual_output.monitor (the actual microphone that Teams/Zoom sees)")
    print("")
    print("To ensure stable operation after restarts:")
    print("1. Make sure your Home Manager configuration includes the rt-translator module")
    print("2. Apply your Home Manager configuration:")
    print("   home-manager switch")
    print("3. Restart PipeWire if needed (after system restart):")
    print("   systemctl --user restart pipewire pipewire-pulse")
    print("")
    print("To verify the virtual sinks exist:")
    print("   pactl list sinks short")
    print("   pactl list sources short")
    print("")
    print("To start the development environment:")
    print("   nix develop")
    print("   python3 -m src.main")
    print("")
    print("If the sinks don't appear, ensure that:")
    print("1. You're using the updated flake.nix with the PipeWire configuration")
    print("2. Your Home Manager configuration is applied and active")
    print("3. PipeWire is running")


if __name__ == "__main__":
    install_pipewire_config()
