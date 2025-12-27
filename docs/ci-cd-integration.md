# CI/CD Integration for 3-Layer Testing Approach

This document outlines the CI/CD integration approach for the 3-layer testing strategy implemented in the real-time translation system. The approach ensures comprehensive validation of the Nix flake, build processes, and runtime behavior.

## Overview

The CI/CD pipeline is designed to validate the system through three distinct testing layers:

1. **Layer 1: Nix/Flake Integrity (Static)** - Validates flake structure and configuration
2. **Layer 2: Build/Packaging** - Validates that packages build successfully
3. **Layer 3: Runtime/System** - Validates runtime behavior and system integration

## CI/CD Pipeline Structure

### Stage 1: Nix/Flake Integrity Tests

**Purpose**: Validate flake structure, option wiring, perSystem correctness, and service/package declarations

**Tools**: 
- `nix flake check`
- nix-unit tests
- flake-parts debug (manual inspection)

**Execution**:
```bash
# Validate flake integrity
nix flake check
```

**Expected Outcome**: All nix-unit tests pass, confirming structural integrity of the flake

### Stage 2: Build/Packaging Tests

**Purpose**: Validate that all packages build successfully and dependencies are properly isolated

**Tools**:
- `nix flake check` (includes build tests)
- Custom build validation tests

**Execution**:
```bash
# Run build validation tests
nix flake check
```

**Expected Outcome**: All packages build successfully without errors

### Stage 3: Runtime/System Tests

**Purpose**: Validate runtime behavior, IPC communication, and system integration

**Tools**:
- pytest
- systemd tests
- socket communication tests

**Execution**:
```bash
# Run runtime tests
python -m pytest tests/systemd-update/ -v
# Run full test suite
python -m pytest tests/ -v
```

**Expected Outcome**: All runtime tests pass, confirming system functionality

## Complete CI/CD Workflow

### GitHub Actions Example

```yaml
name: "Test Suite"

on:
  push:
    branches: [ main, develop ]
  pull_request:
    branches: [ main ]

jobs:
  nix-flake-validation:
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v3
    - uses: DeterminateSystems/nix-installer-action@main
    - name: Check flake
      run: nix flake check
    - name: Run nix-unit tests
      run: nix flake check

  build-validation:
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v3
    - uses: DeterminateSystems/nix-installer-action@main
    - name: Build all packages
      run: |
        nix build .#capture
        nix build .#whisper
        nix build .#translate
        nix build .#tts
        nix build .#playback
        nix build .#app

  runtime-tests:
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v3
    - uses: DeterminateSystems/nix-installer-action@main
    - name: Setup development environment
      run: nix develop --command bash -c "echo 'Environment ready'"
    - name: Run runtime tests
      run: nix develop --command python -m pytest tests/systemd-update/ -v
    - name: Run full test suite
      run: nix develop --command python -m pytest tests/ -k "not gpu" -v

  integration-tests:
    runs-on: ubuntu-latest
    needs: [nix-flake-validation, build-validation]
    steps:
    - uses: actions/checkout@v3
    - uses: DeterminateSystems/nix-installer-action@main
    - name: Run integration tests
      run: nix develop --command python -m pytest tests/test_integration.py -v
```

### GitLab CI Example

```yaml
stages:
  - validate
  - build
  - test

variables:
  FLAKE_DIR: "."

nix-flake-validation:
  stage: validate
  image: nixos/nix:latest
  script:
    - nix flake check
  artifacts:
    when: on_failure
    paths:
      - "*.md"  # Capture any test output files

build-packages:
  stage: build
  image: nixos/nix:latest
  script:
    - nix build .#capture
    - nix build .#whisper
    - nix build .#translate
    - nix build .#tts
    - nix build .#playback
  artifacts:
    when: on_failure
    paths:
      - "*.drv"  # Capture build logs if needed

runtime-tests:
  stage: test
  image: nixos/nix:latest
  script:
    - nix develop --command python -m pytest tests/systemd-update/ -v
    - nix develop --command python -m pytest tests/ -k "not gpu" -v
  artifacts:
    reports:
      junit: test-results.xml
    when: always
    paths:
      - test-results.xml
      - tests/*.md
```

## Separation of Concerns in CI/CD

### Nix/Flake Layer
- Runs first to validate structural integrity
- Fast execution, catches configuration errors early
- Does not require runtime environment setup
- Validates build system configuration

### Build Layer
- Validates that packages can be built successfully
- Ensures dependencies are properly declared
- Runs after flake validation passes
- Catches packaging and dependency issues

### Runtime Layer
- Validates actual runtime behavior
- Tests IPC, audio, TTS, and UI components
- Requires full development environment
- Runs after build validation passes

## Parallel Execution Strategy

For faster CI/CD execution, layers 1 and 2 can run in parallel:

```
        Start
          |
    ┌─────┴─────┐
    │           │
   Nix        Build   (Run in parallel)
   Check      Check
    │           │
    └─────┬─────┘
          │
    Runtime Tests
          │
        Done
```

## Cache and Optimization Strategies

### Nix Caching
- Use Cachix or similar for binary cache
- Configure `~/.config/nix/nix.conf` for optimal caching
- Cache flake evaluation results

### Test Result Caching
- Cache test results to avoid re-running unchanged tests
- Use pytest's cache directory
- Store test artifacts for debugging

### Environment Caching
- Cache Nix development environment
- Use `nix develop --impure` for faster environment loading
- Pre-build common dependencies

## Quality Gates

### Layer 1 (Nix/Flake) Quality Gates
- All nix-unit tests must pass
- Flake must evaluate without errors
- All expected packages must be declared

### Layer 2 (Build) Quality Gates
- All packages must build successfully
- No dependency conflicts
- Package metadata is correct

### Layer 3 (Runtime) Quality Gates
- All runtime tests must pass
- Code coverage meets minimum threshold
- Performance benchmarks are within limits

## Notifications and Reporting

### Success Notifications
- Notify on successful completion of all layers
- Provide build artifacts and test results
- Update status badges

### Failure Notifications
- Immediate notification on any layer failure
- Detailed error reports
- Links to full logs

## Security Considerations

- Scan Nix inputs for security vulnerabilities
- Validate package signatures
- Isolate test environments
- Use read-only file systems where possible

This CI/CD approach ensures comprehensive validation of the real-time translation system while maintaining clear separation between the different testing layers.