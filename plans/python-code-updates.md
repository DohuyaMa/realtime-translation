# Python Code Updates Plan

## Purpose
Update Python code to use dynamic socket paths instead of hardcoded `/tmp` paths by implementing a centralized runtime configuration system.

## Scope
This plan modifies all Python service files to use dynamic socket paths from the new runtime configuration module. It replaces hardcoded `/tmp/rt-*.sock` paths with environment-controlled paths.

## Entry Points
- src/core/runtime.py
- src/capture/capture_service.py
- src/playback/playback_service.py
- src/tts/tts_service.py
- src/translate/translate_service.py
- src/whisper/whisper_service.py
- src/whisper/hybrid_whisper_service.py
- src/translation_system.py
- src/pipeline/orchestrator.py
- src/adapters/ipc_adapter.py

## Planned Changes
- [ ] Create runtime configuration module in src/core/runtime.py
- [ ] Update all service files to use dynamic socket paths
- [ ] Update core components that use hardcoded paths
- [ ] Test each service individually for proper socket creation

## Test Coverage
- Verify services create sockets in correct location → integration test
- Verify IPC communication with new paths → integration test
- Verify services work in different environments → integration test

## Dependencies
### Hard Dependencies
- depends on: runtime-configuration-plan.md

### Soft Dependencies
- should be reviewed after: python-package-definitions.md

## Obsolete / To Be Removed
- Hardcoded /tmp/rt-*.sock paths in Python code
- Direct path concatenation in service files

## Architectural Invariants
- runtime paths XDG-compliant (%t/rt/)
- centralized configuration for all socket paths
- environment-controlled path resolution

## Target Audience
- developers
- maintainers

## Per-File Focus
src/core/runtime.py:
- Centralized runtime configuration
- Dynamic path resolution

Service files (capture, playback, tts, etc.):
- Use runtime config for socket paths
- Maintain backward compatibility

## Completion Criteria
- all Planned Changes checked
- tests listed in Test Coverage are green
- Obsolete items removed or explicitly deprecated

## Post-Implementation Notes
- What changed from original plan
- What was removed as obsolete
- What should be refactored later

## Completed / Coordinating with Parallel Execution

### Completed Items
- [x] Create runtime configuration module in src/core/runtime.py — Runtime configuration module with XDG-compliant paths implemented by Roo
- [x] Create Nix modules for runtime configuration in parts/runtime/ — Created paths.nix, sockets.nix, env.nix, and systemd.nix modules by Roo
- [x] Update all Python services to use dynamic socket paths — All services updated to use runtime configuration by Roo
- [x] Update systemd socket configurations to use new paths — Verified systemd configs already use XDG-compliant paths by Roo
- [x] Test each service individually for proper socket creation — Verified socket paths are correctly configured by Roo

### Notes for Parallel Execution
- Runtime configuration module is now available for all services
- Nix modules in parts/runtime/ provide configuration for build system
- All hardcoded paths have been replaced with runtime-configured paths
- Systemd socket configurations already compliant with XDG standards
- Ready for integration testing and deployment