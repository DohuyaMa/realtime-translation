# Modular Flake Configuration

This document explains the modular flake configuration that has been implemented to improve the organization and maintainability of the Nix build system for the real-time translation system.

## Overview

The original monolithic `flake.nix` file has been refactored into a modular structure using flake-parts. This approach separates concerns and makes it easier to manage different aspects of the build configuration.

## Directory Structure

```
flake-global/
├── flake.nix              # Main flake using flake-parts
├── flake-parts.nix        # Flake-parts configuration framework
├── home-manager-module.nix # Home Manager module for the application
├── prod/                  # Production-specific configurations
│   ├── packages.nix       # Production packages
│   └── apps.nix           # Application definitions
└── dev/                   # Development-specific configurations
    └── devshell.nix       # Development shell environment
```

## Components

### Main Flake (`flake-global/flake.nix`)
- Uses flake-parts to organize the modular configuration
- Defines system targets for multiple architectures
- Imports production and development configurations

### Production Configuration (`flake-global/prod/`)
- **packages.nix**: Defines all production packages including:
  - The main application package
  - Service wrappers for each component (capture, playback, translate, TTS, whisper)
  - Python environment with all required dependencies
- **apps.nix**: Defines application entry points

### Development Configuration (`flake-global/dev/`)
- **devshell.nix**: Creates a development shell with:
  - All necessary system dependencies
  - Python environment with development packages
  - Environment variables for caching
  - Setup hooks for PipeWire virtual sinks

### Home Manager Module (`flake-global/home-manager-module.nix`)
- Contains the Home Manager module for the real-time translation system
- Defines systemd user services and sockets
- Configures PipeWire virtual sinks
- Sets up the application in user environment

## Benefits of Modular Structure

1. **Separation of Concerns**: Production and development configurations are separated
2. **Maintainability**: Each component has its own file, making changes easier to track
3. **Reusability**: Components can be reused or selectively imported
4. **Readability**: Each file has a clear purpose and focused responsibility
5. **Scalability**: New components can be added without bloating a single file

## Integration with Main Flake

The main `flake.nix` file now imports the modular structure through a flake input:

```nix
inputs = {
  # ... other inputs
  flake-global = {
    url = "path:./flake-global";
    inputs.nixpkgs.follows = "nixpkgs";
    inputs.home-manager.follows = "home-manager";
  };
};
```

This maintains backward compatibility while providing the benefits of the modular approach.

## Usage

### For Development
```bash
nix develop  # Uses the devShell defined in flake-global/dev/devshell.nix
```

### For Production Builds
```bash
nix build   # Builds packages defined in flake-global/prod/packages.nix
nix run     # Runs the application defined in flake-global/prod/apps.nix
```

### For Home Manager Integration
The Home Manager module can be used by importing `flake-global.home-manager-module.nix`.

## Migration Notes

- All original functionality is preserved
- The main `flake.nix` now acts as an integration point for the modular components
- Existing build commands continue to work as before
- The modular structure allows for more granular testing and development of individual components