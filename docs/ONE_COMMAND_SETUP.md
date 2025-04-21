# One-Command Setup Implementation Plan

## Overview

To enable one-command setup of the Real-Time Translator system, we need to enhance the `run.sh` script to handle all necessary setup steps automatically.

## Required Changes

### 1. Virtual Audio Device Setup

```bash
# Add to run.sh
setup_virtual_devices() {
    # Create config directory if it doesn't exist
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
    
    # Reload PipeWire to apply changes
    systemctl --user restart pipewire
}
```

### 2. Default Configuration Setup

```bash
# Add to run.sh
setup_default_config() {
    local config_dir="$HOME/.config/real-time-translator"
    mkdir -p "$config_dir"
    
    # Create default configuration
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
```

### 3. First-Time Setup Detection

Add logic to detect if this is the first time running the application:

```bash
# Add to run.sh
is_first_run() {
    # Check if config exists
    [ ! -f "$HOME/.config/real-time-translator/config.yml" ] || \
    # Check if virtual devices are configured
    [ ! -f "$HOME/.config/pipewire/virtual-devices.conf" ] || \
    # Check if Kokoro is set up
    [ ! -d "$HOME/.local/share/real-time-translator/models/kokoro" ]
}
```

### 4. Enhanced Run Script Structure

The enhanced run.sh should:

1. Check for first-time run
2. Set up virtual audio devices if needed
3. Create default configuration if needed
4. Handle Kokoro setup
5. Install/update dependencies
6. Start the application

## Implementation Steps

1. Switch to Code mode to implement these changes in `run.sh`
2. Update the configuration handling in `src/main.py` to work with the new setup
3. Add proper error handling and user feedback
4. Test the setup process on a fresh system

## Success Criteria

1. User can start the application with a single command: `./run.sh`
2. All necessary components are set up automatically:
   - Virtual audio devices
   - Default configuration
   - Kokoro integration
   - Required models
3. Clear feedback is provided during the setup process
4. Existing setups are not disrupted

## Required Code Mode Changes

1. Update `run.sh` with the new setup functions
2. Add error handling and progress indicators
3. Ensure proper cleanup on failure

## Next Steps

1. Switch to Code mode
2. Implement the changes in `run.sh`
3. Test the setup process
4. Update documentation