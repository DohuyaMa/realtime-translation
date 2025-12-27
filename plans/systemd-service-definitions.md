# Systemd Service Definitions Plan

## Purpose
Create systemd service definitions using the new flake-parts structure, replacing the current inline definitions in the home-manager module.

## Scope
This plan creates proper Nix modules for systemd services using flake-parts structure. It defines services that use the new buildPythonApplication packages without PYTHONPATH dependencies.

## Entry Points
- parts/systemd/defaults.nix
- parts/systemd/user/capture.nix
- parts/systemd/user/whisper.nix
- parts/systemd/user/translate.nix
- parts/systemd/user/tts.nix
- parts/systemd/user/playback.nix
- parts/systemd/user/hybrid-whisper.nix
- parts/systemd/user/app.nix
- parts/systemd/user/virtual-sinks.nix
- parts/systemd/sockets.nix
- parts/pipewire/config.nix
- parts/defaults.nix

## Planned Changes
- [ ] Create systemd service definitions using flake-parts structure
- [ ] Update services to use new buildPythonApplication packages
- [ ] Remove PYTHONPATH from service definitions
- [ ] Create proper socket configurations with new paths

## Test Coverage
- Verify systemd services start with new packages → integration test (tests/systemd/)
- Verify no PYTHONPATH usage in services → nix flake check
- Verify socket activation works → integration test

## Dependencies
### Hard Dependencies
- depends on: python-package-definitions.md
- blocks: home-manager-updates.md

### Soft Dependencies
- should be reviewed after: systemd-update-plan.md

## Obsolete / To Be Removed
- Inline systemd service definitions in home-manager module
- PYTHONPATH environment variables in services
- Shell script wrapper references in services

## Architectural Invariants
- no PYTHONPATH usage in systemd services
- each service uses separate buildPythonApplication
- runtime paths XDG-compliant (%t/rt%)

## Target Audience
- maintainers
- developers

## Per-File Focus
parts/systemd/user/*.nix:
- Individual service definitions
- Service-specific configurations

parts/systemd/sockets.nix:
- Socket definitions with proper paths
- Socket permissions

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
- [x] Create systemd service definitions using flake-parts structure — Services created in parts/systemd/user/*.nix by Roo
- [x] Update services to use new buildPythonApplication packages — All services now use individual buildPythonApplication packages by Roo
- [x] Remove PYTHONPATH from service definitions — PYTHONPATH dependencies eliminated from all systemd services by Roo
- [x] Create proper socket configurations with new paths — Socket definitions created with XDG-compliant paths by Roo

### Notes for Parallel Execution
- Systemd services now use proper buildPythonApplication packages
- No more PYTHONPATH dependencies in service definitions
- Socket configurations use XDG-compliant runtime paths
- Services are properly modularized in flake-parts structure
- Ready for integration with home-manager module
- All Nix modules created in parts/systemd/ and parts/pipewire/ directories
- Services use separate buildPythonApplication packages instead of PYTHONPATH
- Socket activation properly configured for services that require it
- Virtual sinks service configured for audio routing
- App service created for main application