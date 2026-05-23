# Real-Time Translation Project Navigation Rules Plan

## Purpose
Establish clear navigation and structural rules for the real-time translation project to ensure consistency, maintainability, and proper architectural alignment across all components including Python services, Nix packaging, systemd services, and Pipewire audio routing.

## Scope
This plan defines the structural and navigational conventions for the real-time translation project, covering:
- Python service architecture (capture, whisper, translate, tts, playback, hybrid-whisper, UI)
- Nix flake-parts modular structure
- Systemd service definitions and socket configurations
- Pipewire audio routing and virtual sinks
- Runtime configuration and environment management
- Test coverage and validation requirements

## Entry Points
- flake.nix
- flake-global/flake.nix
- flake-global/parts/python/*.nix
- parts/systemd/user/*.nix
- parts/pipewire/config.nix
- parts/runtime/*.nix
- src/*.py
- src/*/ *.py
- tests/*.py
- docs/*.md
- plans/*.md

## Planned Changes
- [ ] Establish consistent naming conventions across all modules
- [ ] Define clear service boundaries and dependencies
- [ ] Document architectural invariants and constraints
- [ ] Create standardized configuration patterns
- [ ] Align all components with flake-parts architecture
- [ ] Ensure proper test coverage for all components

## Test Coverage
- Verify all Python services start independently → integration test
- Verify systemd service definitions work correctly → nix flake check
- Verify Pipewire audio routing functions properly → integration test
- Verify cross-service communication via IPC → integration test
- Verify UI components load and function → UI test
- Verify translation pipeline works end-to-end → integration test

## Dependencies
### Hard Dependencies
- depends on: python-package-definitions.md
- depends on: systemd-service-definitions.md
- depends on: runtime-configuration-plan.md

### Soft Dependencies
- should be reviewed after: remove-pythonpath-plan.md
- should be coordinated with: home-manager-updates.md
- affects: testing-plan.md

## Obsolete / To Be Removed
- PYTHONPATH-based import mechanisms
- Monolithic Python environment approach
- Inline systemd service definitions in home-manager
- Hardcoded paths in service configurations
- Legacy configuration files

## Architectural Invariants
- no PYTHONPATH usage in any component
- each service has isolated dependencies via buildPythonApplication
- runtime paths follow XDG standards
- services communicate via IPC mechanisms
- audio routing handled through Pipewire
- configuration managed through Nix and runtime config

## Target Audience
- developers
- maintainers
- users

## Per-File Focus
flake.nix:
- Top-level flake definition
- Entry points for all packages and apps
- Cross-module dependencies

flake-global/parts/python/*.nix:
- Individual service package definitions
- Service-specific dependencies
- Console script definitions

parts/systemd/user/*.nix:
- Systemd service definitions
- Service-specific configurations
- Socket activation configurations

parts/pipewire/config.nix:
- Pipewire audio routing configuration
- Virtual sink definitions
- Audio processing pipeline

src/*/*.py:
- Service-specific implementation
- Clear separation of concerns
- Proper error handling and logging

tests/*.py:
- Unit tests for individual components
- Integration tests for service interactions
- End-to-end pipeline validation

## Completion Criteria
- all Planned Changes checked
- tests listed in Test Coverage are green
- Obsolete items removed or explicitly deprecated
- All architectural invariants are enforced
- Navigation rules are consistently applied across all components

## Post-Implementation Notes
- What changed from original plan
- What was removed as obsolete
- What should be refactored later

## Completed / Coordinating with Parallel Execution

### Completed Items
- [ ] Establish consistent naming conventions across all modules
- [ ] Define clear service boundaries and dependencies
- [ ] Document architectural invariants and constraints
- [ ] Create standardized configuration patterns

### Notes for Parallel Execution
- All Python services should use buildPythonApplication with isolated dependencies
- Systemd services must not rely on PYTHONPATH
- Pipewire configuration must be compatible with virtual sinks
- Runtime paths should follow XDG standards (%t/rt-transletor/)
- Services should communicate via IPC adapters
- Configuration should be managed through Nix and runtime config files
- Testing should cover both unit and integration scenarios

## Service Architecture Navigation Rules

### Python Services Structure
```
src/
├── capture/          # Audio capture service
├── whisper/          # Speech recognition service  
├── translate/        # Translation service
├── tts/              # Text-to-speech service
├── playback/         # Audio playback service
├── whisper/          # Hybrid whisper service
├── ui/               # User interface
├── core/             # Core utilities and config
├── adapters/         # IPC and communication adapters
└── pipeline/         # Pipeline orchestrator
```

### Nix Packaging Structure
```
flake-global/parts/python/
├── common.nix        # Shared dependencies
├── capture.nix       # Capture service package
├── whisper.nix       # Whisper service package
├── translate.nix     # Translation service package
├── tts.nix           # TTS service package
├── playback.nix      # Playback service package
├── hybrid-whisper.nix # Hybrid whisper package
└── ui.nix            # UI application package
```

### Systemd Service Structure
```
parts/systemd/user/
├── capture.nix       # Capture service definition
├── whisper.nix       # Whisper service definition
├── translate.nix     # Translation service definition
├── tts.nix           # TTS service definition
├── playback.nix      # Playback service definition
├── hybrid-whisper.nix # Hybrid whisper service
├── app.nix           # Main application service
├── virtual-sinks.nix # Virtual audio sinks
└── defaults.nix      # Default configurations
```

## Configuration Management Rules

### Runtime Configuration
- Configuration files located in `config/` directory
- Runtime paths use XDG standards (`%t/rt-transletor/`)
- Environment variables managed through Nix build process
- Service-specific configs loaded at runtime

### Build Configuration
- Package dependencies defined in respective `parts/python/*.nix` files
- Service entry points defined via console_scripts
- No PYTHONPATH manipulation in build process
- Dependencies isolated per service

## Testing Navigation Rules

### Test Organization
```
tests/
├── unit/             # Unit tests for individual components
├── integration/      # Integration tests for service interactions
├── ui/               # UI component tests
├── pipeline/         # End-to-end pipeline tests
└── systemd/          # Systemd service tests
```

### Test Requirements
- Each Python module must have corresponding unit tests
- Service interactions must be covered by integration tests
- Pipeline functionality must be validated end-to-end
- Systemd service definitions must be tested in isolation

## Documentation Navigation Rules

### Documentation Structure
```
docs/
├── flake-architecture.md     # Nix flake architecture
├── flake-debug-queries.md    # Debugging flake queries
├── flake-modular-structure.md # Modular structure explanation
└── refactored-architecture.md # Refactored architecture overview
```

### Plan Documentation
- Each plan in `plans/` follows standardized template
- Entry points clearly defined for each plan
- Dependencies between plans explicitly stated
- Test coverage requirements specified for each plan

## Error Handling and Logging Navigation

### Logging Structure
- Each service implements consistent logging approach
- Runtime logs stored in XDG-compliant locations
- Error conditions properly reported to UI
- IPC communication errors handled gracefully

### Error Recovery
- Services should restart on failure with exponential backoff
- Configuration errors should provide clear feedback
- Audio pipeline errors should degrade gracefully
- Network translation errors should have offline fallback

## IPC and Communication Navigation

### IPC Architecture
- Services communicate via structured IPC mechanisms
- Direct adapter for same-process communication
- IPC adapter for cross-process communication
- Message formats defined and validated
- Connection management with proper cleanup

## Audio Pipeline Navigation

### Audio Flow
1. Capture service records audio input
2. Whisper service performs speech recognition
3. Translate service converts to target language
4. TTS service generates audio output
5. Playback service renders audio

### Audio Configuration
- Pipewire handles audio routing and virtual sinks
- Sample rates and formats standardized across pipeline
- Latency optimized for real-time performance
- Audio quality maintained throughout pipeline
