# Systemd Update Tests

This directory contains tests for the systemd update implementation of the real-time translation system. These tests validate the new architecture where services are properly packaged and integrated with systemd for better process management and socket activation.

## Test Layers

The tests are organized according to a 3-layer testing approach:

### Layer 1: Nix/Flake Integrity (Static)
- **Purpose**: Validate flake structure, option wiring, perSystem correctness, and service/package declarations
- **Tools**: flake-parts debug, nix repl, nix-unit, nix flake check
- **Coverage**:
  - Verify all services are declared in flake outputs
  - Verify perSystem packages exist
  - Verify systemd module wiring
  - Verify no implicit PYTHONPATH or shared env

### Layer 2: Build/Packaging
- **Purpose**: Validate that packages build successfully and dependencies are properly isolated
- **Tools**: nix flake check, build tests (build-test.nix)
- **Coverage**:
  - `buildPythonApplication` builds successfully
  - Entrypoints exist
  - Dependencies are isolated

### Layer 3: Runtime/System
- **Purpose**: Validate runtime behavior, IPC communication, and system integration
- **Tools**: pytest, systemd tests, socket tests
- **Coverage**:
  - IPC (Inter-Process Communication)
  - Audio processing
  - TTS (Text-to-Speech)
  - UI components
  - Real services

## Test Files

- `build-test.nix`: Nix-unit tests for package build validation
- `runtime-test.py`: Runtime configuration validation tests
- `socket-test.py`: Socket communication validation tests

## Running Tests

### Nix/Flake Integrity Tests
```bash
nix flake check
```

### Build Tests
```bash
nix flake check  # Includes build tests
```

### Runtime Tests
```bash
python -m pytest tests/systemd-update/
```

## Integration with Existing Test Suite

The systemd-update tests work alongside the existing test suite:
- Nix-unit tests run with `nix flake check`
- Python runtime tests run with `pytest`
- CI runs both test suites in sequence: `nix flake check` followed by `pytest`

Do not mix pytest with nix-unit - they serve different architectural layers.