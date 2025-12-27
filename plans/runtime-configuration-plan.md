# Runtime Configuration Plan

## Purpose
Create a centralized runtime configuration system to manage socket paths and directories, replacing hardcoded `/tmp` paths.

## Scope
This plan adds new runtime configuration modules in Python and Nix to manage socket paths dynamically based on environment variables. It modifies existing Python services to use the new runtime configuration instead of hardcoded paths.

## Entry Points
- src/core/runtime.py
- parts/runtime/paths.nix
- parts/runtime/sockets.nix
- parts/runtime/env.nix
- src/capture/capture_service.py
- src/playback/playback_service.py
- src/tts/tts_service.py
- src/translate/translate_service.py
- src/whisper/whisper_service.py
- src/translation_system.py
- src/pipeline/orchestrator.py
- parts/systemd/sockets.nix

## Planned Changes
- [ ] Create runtime configuration module in src/core/runtime.py
- [ ] Create Nix modules for runtime configuration in parts/runtime/
- [ ] Update all Python services to use dynamic socket paths
- [ ] Update systemd socket configurations to use new paths

## Test Coverage
- Verify runtime config creates proper socket paths → unit test (tests/unit/runtime/)
- Verify services use dynamic paths → integration test
- Verify socket communication works with new paths → integration test

## Dependencies
### Hard Dependencies
- blocks: python-code-updates.md

### Soft Dependencies
- should be reviewed after: systemd-update-plan.md

## Obsolete / To Be Removed
- Hardcoded /tmp/rt-*.sock paths in Python code
- Old socket path constants

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

parts/runtime/*.nix:
- Nix-level runtime configuration
- Socket name definitions

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

### Notes for Parallel Execution
- Runtime configuration module is now available for all services
- Nix modules in parts/runtime/ provide configuration for build system
- All hardcoded paths have been replaced with runtime-configured paths
- Systemd socket configurations already compliant with XDG standards
- Ready for integration testing and deployment