# Python Package Definitions Plan

## Purpose
Create individual Python package definitions for each service using `buildPythonApplication` to eliminate monolithic python environment and remove PYTHONPATH dependencies.

## Scope
This plan creates separate `buildPythonApplication` derivations for each service, with only the dependencies each service actually needs. It replaces the current monolithic python environment approach.

## Entry Points
- parts/python/common.nix
- parts/python/capture.nix
- parts/python/whisper.nix
- parts/python/translate.nix
- parts/python/tts.nix
- parts/python/playback.nix
- parts/python/hybrid-whisper.nix
- parts/python/ui.nix
- src/capture/capture_service.py
- src/whisper/whisper_service.py
- src/translate/translate_service.py
- src/tts/tts_service.py
- src/playback/playback_service.py
- src/whisper/hybrid_whisper_service.py
- src/main.py

## Planned Changes
- [x] Create individual buildPythonApplication definitions for each service
- [x] Separate dependencies for each service
- [x] Update service entry points to work with console scripts
- [ ] Test that each package builds and runs correctly

## Test Coverage
- Verify each buildPythonApplication builds successfully → nix flake check
- Verify console scripts work correctly → integration test
- Verify services have only required dependencies → nix flake check

## Dependencies
### Hard Dependencies
- blocks: systemd-service-definitions.md
- blocks: home-manager-updates.md

### Soft Dependencies
- should be reviewed after: remove-pythonpath-plan.md

## Obsolete / To Be Removed
- Monolithic pythonEnv in home-manager module
- PYTHONPATH environment variables
- Shell script wrappers

## Architectural Invariants
- each service = separate buildPythonApplication
- no PYTHONPATH usage
- dependencies are service-specific

## Target Audience
- developers
- maintainers

## Per-File Focus
parts/python/*.nix:
- Individual service packages
- Service-specific dependencies
- Console script definitions

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
- [x] Task 1 — Created individual buildPythonApplication definitions for each service
- [x] Task 2 — Separated dependencies for each service
- [x] Task 3 — Updated service entry points to work with console scripts
- [ ] Task 4 — Test that each package builds and runs correctly

### Notes for Parallel Execution
- Task 4 (testing) can be executed after all package definitions are in place
- Dependencies with systemd-service-definitions.md and home-manager-updates.md should be coordinated before full implementation
- Each service package definition has been implemented separately in parts/python/*.nix files
- Console scripts have been defined for each service to replace shell script wrappers