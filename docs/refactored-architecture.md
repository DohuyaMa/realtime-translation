# Real-time Speech Translation System - Refactored Architecture

## Overview

This document describes the refactored architecture of the Real-time Speech Translation System, which includes:

- Modular Nix flake with proper Home Manager integration
- Controller pattern for UI/backend separation
- Event-based updates instead of polling
- Separation of status display from logging
- Proper service status panel abstraction
- Kirigami integration preparation

## Nix Flake Architecture

### Issues Fixed

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
   - Fixed PYTHONPATH issues that caused `ModuleNotFoundError`

## Controller Pattern Architecture

### Components

1. **Controller Interface** (`src/controller/controller.py`)
   - Defines abstract interface for the translation system
   - Provides methods for pipeline control, service management, status queries, etc.

2. **Concrete Controller** (`src/controller/translator_controller.py`)
   - Implements the controller interface using an adapter

3. **Adapters**
   - **DirectAdapter** (`src/adapters/direct_adapter.py`): Wraps the existing TranslationSystem
   - **IPCAdapter** (`src/adapters/ipc_adapter.py`): Communicates via Unix sockets

### Benefits

- Complete separation of UI from backend logic
- Easy switching between direct and IPC modes
- Testable components
- Preparation for Kirigami integration

## UI Architecture

### Components

1. **Status Logger** (`src/ui/widgets/status_logger.py`)
   - Separates status display from logging
   - Provides scrollable log view
   - Status manager for centralized status handling

2. **Service Status Panel** (`src/ui/widgets/service_status_panel.py`)
   - Dedicated widget for service status
   - Proper abstraction from main UI logic
   - Signal-based communication

3. **Main Window** (`src/ui/widgets/main_window.py`)
   - Uses controller pattern
   - Event-based updates instead of polling
   - Proper Qt quit instead of sys.exit(0)

## Event-Based Updates

- Replaced 100ms polling with event-based updates
- Background polling thread for status changes
- Fallback timer for robustness
- Better performance and battery life

## Kirigami Integration

### Structure

- **QML Files** (`src/ui/qml/`)
  - `Main.qml`: Application window
  - `Dashboard.qml`: Main dashboard page
- **Python Entry Point** (`src/ui/qml/main.py`): QML-Python bridge

### Features

- Uses PySide6 instead of PyQt6 for better Kirigami support
- QML-first architecture
- IPC-ready for systemd integration
- Wayland-native

## Testing the Refactored Architecture

To test the refactored architecture:

1. **Nix Flake**
   ```bash
   nix flake check
   nix build
   nix run
   ```

2. **Python Controller Pattern**
   ```bash
   python -c "from src.controller import ConcreteTranslatorController; from src.adapters import DirectAdapter; controller = ConcreteTranslatorController(DirectAdapter()); print(controller.get_status())"
   ```

3. **Kirigami UI** (if PySide6 and Kirigami are available)
   ```bash
   cd src/ui/qml
   python main.py
   ```

## Future Improvements

1. **Complete IPC Implementation**
   - Implement full IPC communication between UI and backend
   - Socket activation for services

2. **Kirigami UI Completion**
   - Connect QML UI to the controller
   - Implement full functionality in QML

3. **Service Status Panel Enhancements**
   - Real-time status updates
   - Better visual feedback

4. **Audio Device Handling**
   - Enhanced device selection
   - Real-time device detection