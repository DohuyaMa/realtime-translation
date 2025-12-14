#!/usr/bin/env python3
"""Script to install the systemd service for virtual sinks for the real-time translation system."""

import os
import sys
import shutil
from pathlib import Path


def install_pipewire_config():
    """Install the systemd service file to create virtual sinks automatically."""
    # Get user's home directory
    home_dir = Path.home()
    
    # Define source and destination paths for systemd service
    source_service = Path("systemd/rt-virtual-sinks.service")
    systemd_user_dir = home_dir / ".config" / "systemd" / "user"
    destination_service = systemd_user_dir / "rt-virtual-sinks.service"
    
    # Check if source file exists
    if not source_service.exists():
        print(f"Error: Source service file {source_service} not found.")
        print("Make sure you're running this script from the project root directory.")
        sys.exit(1)
    
    # Create destination directory if it doesn't exist
    systemd_user_dir.mkdir(parents=True, exist_ok=True)
    
    # Copy the service file
    try:
        shutil.copy2(source_service, destination_service)
        print(f"Successfully copied systemd service to {destination_service}")
    except Exception as e:
        print(f"Error copying service file: {e}")
        sys.exit(1)
    
    # Enable and start the service
    try:
        import subprocess
        # Reload systemd daemon
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        print("Systemd daemon reloaded")
        
        # Enable and start the service
        subprocess.run(["systemctl", "--user", "enable", "rt-virtual-sinks.service"], check=True)
        subprocess.run(["systemctl", "--user", "start", "rt-virtual-sinks.service"], check=True)
        print("Virtual sinks service enabled and started successfully")
    except subprocess.CalledProcessError as e:
        print(f"Warning: Failed to enable/start service: {e}")
        print("You may need to enable and start the service manually.")
    except FileNotFoundError:
        print("Warning: systemctl command not found.")
        print("Make sure you have systemd installed.")
    
    # Verify the virtual devices were created
    try:
        result_sinks = subprocess.run(["pactl", "list", "sinks", "short"],
                              capture_output=True, text=True, check=True)
        result_sources = subprocess.run(["pactl", "list", "sources", "short"],
                               capture_output=True, text=True, check=True)
        
        sinks_ok = "rt_virtual_input" in result_sinks.stdout and "rt_virtual_output" in result_sinks.stdout
        sources_ok = "rt_virtual_output.monitor" in result_sources.stdout
        
        if sinks_ok and sources_ok:
            print("✓ Virtual devices created successfully")
            print("  - rt_virtual_input (available as sink)")
            print("  - rt_virtual_output (available as sink)")
            print("  - rt_virtual_output.monitor (available as source/microphone for Teams/Zoom)")
        else:
            print("⚠ Warning: Virtual devices may not have been created properly")
            if not sinks_ok:
                print("  - Sinks missing")
            if not sources_ok:
                print(" - Monitor source missing (rt_virtual_output.monitor)")
            print("Please check the service status with: systemctl --user status rt-virtual-sinks.service")
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("⚠ Warning: Could not verify virtual devices")
    
    print(f"\nVirtual sinks service installed successfully!")
    print("The service will automatically create virtual sinks after PipeWire starts.")
    print("Virtual devices:")
    print("  - rt_virtual_input (sink where Python writes sound)")
    print("  - rt_virtual_output (sink for Teams/Zoom to use as mic)")
    print("  - rt_virtual_output.monitor (the actual microphone that Teams/Zoom sees)")


if __name__ == "__main__":
    install_pipewire_config()