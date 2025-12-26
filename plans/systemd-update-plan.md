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
- [ ] Create new flake-parts structure in flake-global/parts/
- [ ] Update Python code to use dynamic socket paths instead of hardcoded /tmp paths
- [ ] Create individual Python package definitions using buildPythonApplication
- [ ] Create systemd service definitions using the new flake-parts structure
- [ ] Update home-manager module to use the new systemd parts structure
- [ ] Remove PYTHONPATH environment variables from systemd services

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