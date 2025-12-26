# Python Packages Structure Plan

## Purpose
Define the structure for individual Python package definitions using buildPythonApplication to replace the monolithic python environment approach.

## Scope
This plan outlines the approach for creating individual Python package definitions for each service, with proper separation of dependencies and elimination of the shared environment approach.

## Entry Points
- parts/python/common.nix
- parts/python/capture.nix
- parts/python/whisper.nix
- parts/python/translate.nix
- parts/python/tts.nix
- parts/python/playback.nix
- parts/python/hybrid-whisper.nix
- parts/python/ui.nix

## Planned Changes
- [x] Create common dependencies module
- [x] Create individual service packages with specific dependencies
- [x] Define console scripts for each service
- [x] Ensure proper import structure for packaged applications

## Test Coverage
- Verify each package builds independently → nix flake check
- Verify service-specific dependencies are correct → nix flake check
- Verify console scripts are generated properly → integration test

## Dependencies
### Hard Dependencies
- blocks: systemd-service-definitions.md
- blocks: remove-pythonpath-plan.md

### Soft Dependencies
- should be reviewed after: systemd-update-plan.md

## Obsolete / To Be Removed
- Monolithic pythonEnv approach
- Shared environment dependencies
- Runtime dependency mixing

## Architectural Invariants
- each service has isolated dependencies
- no shared environment between services
- self-contained service packages

## Target Audience
- developers
- maintainers

## Per-File Focus
parts/python/common.nix:
- Common dependencies across services
- Shared dependency patterns

parts/python/*service*.nix:
- Service-specific dependencies
- Console script definitions

## Completion Criteria
- all Planned Changes checked
- all packages build successfully
- dependencies are properly isolated

## Post-Implementation Notes
- What changed from original plan
- What was removed as obsolete
- What should be refactored later

## Completed / Coordinating with Parallel Execution

### Completed Items
- [x] Create common dependencies module - parts/python/common.nix created with common dependencies
- [x] Create individual service packages - All service packages created (capture, whisper, translate, tts, playback, hybrid-whisper, ui)
- [x] Define console scripts for each service - Console scripts defined for each service
- [x] Ensure proper import structure - Each service has isolated dependencies with proper import structure

### Notes for Parallel Execution
- These package definitions now replace the monolithic pythonEnv approach
- All services have isolated dependencies preventing conflicts
- Each package can be built independently using buildPythonApplication
- Dependencies are properly separated per service requirements
- This change blocks systemd-service-definitions.md and remove-pythonpath-plan.md as noted in dependencies
- Requires integration testing to verify all services work together