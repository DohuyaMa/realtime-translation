# Home Manager Module Updates Plan

## Purpose
Update the home-manager module to use the new systemd parts structure, replacing the current monolithic inline definitions.

## Scope
This plan updates the home-manager module to import the new flake-parts structure instead of defining services inline. It removes the old service definitions and uses the new modular approach.

## Entry Points
- flake-global/home-manager-module.nix
- flake-global/flake.nix
- parts/defaults.nix

## Planned Changes
- [ ] Update home-manager module to import new parts structure
- [ ] Remove inline systemd service definitions
- [ ] Update module to use new python packages
- [ ] Test that module imports correctly

## Test Coverage
- Verify home-manager module imports properly → nix flake check
- Verify services are defined correctly → integration test
- Verify no old definitions remain → nix flake check

## Dependencies
### Hard Dependencies
- depends on: systemd-service-definitions.md
- depends on: python-package-definitions.md

### Soft Dependencies
- should be reviewed after: systemd-update-plan.md

## Obsolete / To Be Removed
- Inline systemd.user.services definitions
- Inline systemd.user.sockets definitions
- Shell script wrappers
- Monolithic pythonEnv
- PYTHONPATH environment variables

## Architectural Invariants
- no inline service definitions
- uses flake-parts structure
- no PYTHONPATH usage

## Target Audience
- maintainers
- users

## Per-File Focus
flake-global/home-manager-module.nix:
- Import new parts structure
- Remove old definitions
- Use new python packages

## Completion Criteria
- all Planned Changes checked
- tests listed in Test Coverage are green
- Obsolete items removed or explicitly deprecated

## Post-Implementation Notes
- What changed from original plan
- What was removed as obsolete
- What should be refactored later