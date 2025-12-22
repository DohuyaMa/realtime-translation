# Nix Flake Architecture Documentation

## Overview

This document describes the architecture of the refactored `flake.nix` file for the Real-time Speech Translation System.

## Structure

The flake is organized into the following main components:

### 1. Inputs
- `nixpkgs`: Core Nix packages (nixos-25.11)
- `flake-utils`: Utility functions for flake operations
- `home-manager`: Home Manager integration (follows nixpkgs)

### 2. Home Manager Module
- Defined as `homeManagerModules.rt-translator`
- Provides systemd user services and socket activation
- Uses proper Python environment for runtime
- Separated from `eachSystem` block to avoid system dependency

### 3. System-Specific Outputs
- Uses `flake-utils.lib.eachSystem` for x86_64-linux
- Provides devShells, packages, and apps

## Key Improvements

### Fixed Issues from Original Flake

1. **Home Manager Integration**
   - Added missing `home-manager` input
   - Created proper `homeManagerModules.rt-translator` instead of invalid `homeManagerConfiguration`
   - Moved HM configuration outside of `eachSystem`

2. **Systemd Services**
   - Fixed Python environment usage with proper runtime wrappers
   - Corrected `rt-playback` service module path from `src.playback_service` to `src.playback.playback_service`
   - Removed GUI dependencies from systemd services
   - Used proper pipewire commands for audio virtual sinks

3. **Audio Stack**
   - Replaced conflicting pulseaudio/pipewire usage with proper pipewire commands
   - Used `pactl` from pipewire instead of pulseaudio directly

4. **Python Packaging**
   - Replaced incorrect `buildPythonPackage` with proper `mkDerivation`
   - Added `buildPhase = "true"` to skip unnecessary build steps
   - Fixed PYTHONPATH in runtime wrappers

5. **Runtime Environment**
   - Created proper service wrappers with correct Python environment
   - Fixed PYTHONPATH issues that caused `ModuleNotFoundError`
   - Added proper environment variables in devShell

## Home Manager Module Details

The `rtTranslatorModule` provides:

### Systemd User Services
- `rt-capture`: Audio capture service with socket activation
- `rt-playback`: Audio playback service with socket activation
- `rt-translate`: Translation service with socket activation
- `rt-tts`: Text-to-speech service with socket activation
- `rt-whisper`: Speech recognition service with socket activation
- `rt-virtual-sinks`: PipeWire virtual audio devices

### Systemd User Sockets
- Socket files for each service enabling socket activation

### Runtime Dependencies
- Proper Python environment with all required packages
- Service wrapper executables

## Development Environment

The `devShells.default` provides:
- All required Python packages
- System dependencies for development
- Proper environment variables for Hugging Face models
- Setup hooks for cache directories

## Packages and Apps

- `packages.default`: The main application package
- `apps.default`: Runnable application with proper wrapper

## Audio Configuration

The system uses PipeWire for audio routing with virtual sinks:
- `rt_virtual_input`: Virtual input device
- `rt_virtual_output`: Virtual output device (configured as microphone)

## Runtime Wrapper Pattern

Each service uses a runtime wrapper pattern:
```nix
serviceWrapper = name: modulePath: pkgs.writeShellApplication {
  name = "rt-${name}-service";
  runtimeInputs = [ pythonEnv pkgs.coreutils ];
  text = ''
    export PYTHONPATH="${pythonEnv}/lib/python3.12/site-packages:$PYTHONPATH"
    exec ${pythonEnv.interpreter} -m ${modulePath} "$@"
  '';
};
```

This ensures services run with the correct Python environment and dependencies.