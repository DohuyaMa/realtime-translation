# Systemd Update Plan

## Purpose
Update the systemd architecture to use flake-parts structure with proper socket paths and eliminate PYTHONPATH dependencies.

## Scope
This plan modifies the systemd service definitions, updates runtime configurations, and restructures the flake to use proper Nix modules instead of hardcoded paths. The plan adds new parts structure while removing the old monolithic approach.

## Entry Points
- flake-global/flake.nix
- flake-global/home-manager-module.nix
- systemd/*.service
- systemd/*.socket
- parts/python/*.nix
- parts/systemd/*.nix
- parts/runtime/*.nix
- src/*.py (socket path references)

## Planned Changes
- [x] Create new flake-parts structure in flake-global/parts/
- [x] Update Python code to use dynamic socket paths instead of hardcoded /tmp paths
- [x] Create individual Python package definitions using buildPythonApplication
- [x] Create systemd service definitions using the new flake-parts structure
- [x] Update home-manager module to use the new systemd parts structure
- [x] Remove PYTHONPATH environment variables from systemd services

## Test Coverage
- Verify systemd services start with new socket paths → integration test (tests/systemd/)
- Verify socket communication works → unit test (tests/unit/runtime/)
- Verify no PYTHONPATH usage → nix flake check

## Dependencies
### Hard Dependencies
- depends on: runtime-configuration-plan.md
- depends on: python-package-definitions.md

### Soft Dependencies
- should be reviewed after: systemd-service-definitions.md

## Obsolete / To Be Removed
- systemd/*.service files (replaced by flake-parts)
- systemd/*.socket files (replaced by flake-parts)
- PYTHONPATH environment variables in services
- shell script wrappers
- monolithic pythonEnv in home-manager module

## Architectural Invariants
- no PYTHONPATH usage in systemd services
- runtime paths XDG-compliant (%t/rt/)
- each service = separate buildPythonApplication

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
- [x] Create new flake-parts structure in flake-global/parts/ — Implemented in flake-global/flake.nix and parts/ directory
- [x] Update Python code to use dynamic socket paths instead of hardcoded /tmp paths — Runtime configuration system implemented in src/core/runtime.py
- [x] Create individual Python package definitions using buildPythonApplication — All Python services now have individual buildPythonApplication definitions
- [x] Create systemd service definitions using the new flake-parts structure — Systemd services created in parts/systemd/
- [x] Update home-manager module to use the new systemd parts structure — Home-manager module updated to use new architecture
- [x] Remove PYTHONPATH environment variables from systemd services — PYTHONPATH dependencies removed, using proper package structure instead

### Notes for Parallel Execution
- Systemd services now use XDG-compliant runtime paths (%t/rt/)
- All socket communication happens through the new runtime configuration system
- Python services are now properly packaged without requiring PYTHONPATH
- Flake-parts structure provides modular and maintainable architecture
- Ready for integration testing and deployment