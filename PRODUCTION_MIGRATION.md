# Development and Production Migration Guide

This document explains how to set up both development and production environments using the flake modules, with focus on stable operation after restarts.

## Development Setup (Stable After Restarts)

For development with stable operation after system restarts, use the Home Manager module:

```nix
# In your home.nix or home-manager configuration
{
  imports = [
    inputs.rt-translator.homeManagerModules.rt-translator
  ];

  rt-translator.enable = true;
}
```

This sets up:
- User-level systemd services for the various components
- PipeWire configuration via user config file (creates rt_virtual_input and rt_virtual_output automatically)
- Development environment with all necessary dependencies

### After System Restart

To ensure the dev environment works stably after restarts:

1. Make sure your Home Manager configuration is applied:
   ```bash
   home-manager switch
   ```

2. Restart PipeWire to ensure virtual sinks are created:
   ```bash
   systemctl --user restart pipewire pipewire-pulse
   ```

3. Verify the virtual sinks exist:
   ```bash
   pactl list sinks short
   pactl list sources short
   ```
4. Start the development environment:
   ```bash
   nix develop
   python3 -m src.main
   ```

## Production Setup

For production, use the NixOS module:

```nix
# In your NixOS configuration
{
  imports = [
    inputs.rt-translator.nixosModules.virtual-sinks
  ];

  rt.audio.virtualSinks = {
    enable = true;
    inputName = "rt_virtual_input";
    outputName = "rt_virtual_output";
  };

  # Optional: Add system-level services for automatic startup
  # This would be implemented separately based on your needs
}
```

This sets up:
- System-level PipeWire configuration
- Virtual sinks available system-wide
- Production-ready audio topology

## Development: Optional Systemd User Services

For automatic startup of services, you can enable the systemd user services:

```bash
# Enable and start the main application service
systemctl --user enable --now rt-app.service

# Or enable individual services if needed
systemctl --user enable --now rt-capture.socket rt-whisper.socket rt-translate.socket rt-tts.socket rt-playback.socket
```

Note: For development, it's often easier to run services manually through the devShell rather than using systemd user services.


## Development Setup

For development, use the Home Manager module:

```nix
# In your home.nix or home-manager configuration
{
  imports = [
    inputs.rt-translator.homeManagerModules.rt-translator
  ];

  rt-translator.enable = true;
}
```

This sets up:
- User-level systemd services for the various components
- PipeWire configuration via user config file
- Development environment with all necessary dependencies

## Production Setup

For production, use the NixOS module:

```nix
# In your NixOS configuration
{
  imports = [
    inputs.rt-translator.nixosModules.virtual-sinks
  ];

  rt.audio.virtualSinks = {
    enable = true;
    inputName = "rt_virtual_input";
    outputName = "rt_virtual_output";
  };

  # Optional: Add system-level services for automatic startup
  # This would be implemented separately based on your needs
}
```

This sets up:
- System-level PipeWire configuration
- Virtual sinks available system-wide
- Production-ready audio topology

## Migration Path

The migration from development to production involves:
1. Switching from Home Manager module to NixOS module
2. The application code remains unchanged
3. Audio topology is handled declaratively through Nix
4. No imperative commands or runtime setup needed

## Benefits

- **Declarative**: Audio topology defined in Nix, not runtime commands
- **Reproducible**: Same configuration in dev and prod
- **Reliable**: No runtime dependencies on executables that might not exist
- **Maintainable**: Clear separation of concerns between audio setup and application logic