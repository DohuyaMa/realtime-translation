# Real-Time Translation System Tests

This directory contains comprehensive tests for the real-time translation system, focusing on Ukrainian and Polish to English translation.

## Test Structure

- `test_audio.py` - Audio processing and routing tests
- `test_ai_models.py` - Speech recognition and TTS tests
- `test_integration.py` - End-to-end system tests
- `conftest.py` - Test configuration and fixtures
- `run_tests.py` - Test runner script

## Running Tests

You can run tests using the provided `run_tests.py` script:

```bash
# Run all tests
python tests/run_tests.py

# Run with coverage report
python tests/run_tests.py --coverage

# Run specific test types
python tests/run_tests.py --unit-only
python tests/run_tests.py --integration-only
python tests/run_tests.py --gpu-only
```

## Test Categories

### Audio Tests
- Audio capture initialization
- Virtual device creation
- Audio routing
- Speech detection
- Audio level monitoring

### AI Model Tests
- Ukrainian speech recognition
- Polish speech recognition
- English TTS synthesis
- Language detection confidence
- Model performance metrics

### Integration Tests
- End-to-end translation pipeline
- Real-time performance
- Continuous operation
- Error recovery
- Device switching
- Language switching

## Required Dependencies

For NixOS, add the following to your `/etc/nixos/configuration.nix`:

```nix
{ config, pkgs, ... }:

{
  # For audio and testing
  environment.systemPackages = with pkgs; [
    # Python and development tools
    python3
    python3Packages.pytest
    python3Packages.pytest-cov
    python3Packages.numpy
    python3Packages.sounddevice
    python3Packages.torch
    
    # Audio tools
    espeak
    pulseaudio
    pavucontrol
  ];

  # Enable sound with pipewire
  sound.enable = true;
  security.rtkit.enable = true;
  services.pipewire = {
    enable = true;
    alsa.enable = true;
    alsa.support32Bit = true;
    pulse.enable = true;
    jack.enable = true;
  };
}
```

After updating configuration:
```bash
sudo nixos-rebuild switch
```

## GPU Testing

GPU tests are automatically skipped if no GPU is available or if PyTorch is not installed with CUDA support.

## Test Data

Test audio files are automatically generated using espeak during test execution. The files are created in `tests/test_data/` and cleaned up after tests complete.

## Coverage Reports

When running with `--coverage`, reports are generated in:
- Terminal output (summary)
- `tests/coverage/` (detailed HTML report)

## Performance Testing

Performance tests verify:
- Real-time processing capability
- Translation latency
- Audio buffer handling
- GPU acceleration (when available)

## Test Environment

Tests automatically:
- Create required directories
- Set up logging
- Configure Python path
- Handle cleanup

## Adding New Tests

When adding new tests:
1. Use appropriate markers:
   - `@pytest.mark.integration` for integration tests
   - `@pytest.mark.gpu` for GPU-dependent tests
2. Add test data if needed
3. Update this documentation

## Troubleshooting

Common issues:
1. Audio device access:
   ```bash
   # Check PipeWire service status
   systemctl --user status pipewire pipewire-pulse
   
   # Start PipeWire if not running
   systemctl --user start pipewire pipewire-pulse
   
   # Check audio group
   groups $USER | grep audio
   ```

2. GPU tests failing:
   Add CUDA support to your NixOS configuration:
   ```nix
   { config, pkgs, ... }:
   {
     # Enable CUDA support
     nixpkgs.config.cudaSupport = true;
     
     environment.systemPackages = with pkgs; [
       # CUDA toolkit
       cudaPackages.cudatoolkit
       cudaPackages.cudnn
       
       # PyTorch with CUDA
       (python3.withPackages (ps: [
         (ps.pytorch.override { cudaSupport = true; })
       ]))
     ];
   }
   ```
   Then rebuild:
   ```bash
   sudo nixos-rebuild switch
   ```

3. Model downloads:
   ```bash
   # Check available space
   df -h ~/.cache/whisper ~/.cache/kokoro
   
   # Clear cache if needed
   rm -rf ~/.cache/whisper/* ~/.cache/kokoro/*
   
   # Ensure Nix store has space
   nix-collect-garbage -d
   ```

4. Integration test timeouts:
   ```bash
   # Check system resources
   free -h
   nix-shell -p htop --run htop
   
   # Increase timeouts in test_integration.py
   # Also consider adjusting system limits:
   systemctl --user edit pipewire
   # Add:
   # [Service]
   # LimitRTPRIO=99
   # LimitNICE=-19
   ```

## Test Maintenance

Regular tasks:
1. Update test data for new languages
2. Verify GPU test configurations
3. Check coverage reports
4. Update performance benchmarks