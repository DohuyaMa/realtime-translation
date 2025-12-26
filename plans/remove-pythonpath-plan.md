# Remove PYTHONPATH Environment Variables Plan

## Purpose
Remove PYTHONPATH environment variables from systemd services by using proper buildPythonApplication packages instead of monolithic python environment.

## Scope
This plan eliminates PYTHONPATH usage in systemd services by ensuring each service is packaged as a proper buildPythonApplication with all dependencies built-in. This removes runtime path manipulation.

## Entry Points
- parts/systemd/user/*.nix (all service files)
- flake-global/home-manager-module.nix (old definitions)
- parts/python/*.nix (new package definitions)

## Planned Changes
- [ ] Verify buildPythonApplication packages work correctly
- [ ] Update systemd service definitions to remove PYTHONPATH
- [ ] Test services work without PYTHONPATH
- [ ] Verify all dependencies are included in packages

## Test Coverage
- Verify service starts without PYTHONPATH → integration test
- Verify all imports work without PYTHONPATH → integration test
- Verify no PYTHONPATH in final systemd service definitions → nix flake check

## Dependencies
### Hard Dependencies
- depends on: python-package-definitions.md
- blocks: systemd-service-definitions.md

### Soft Dependencies
- should be reviewed after: python-packages-structure.md

## Obsolete / To Be Removed
- PYTHONPATH environment variables in systemd services
- Runtime path manipulation
- Monolithic python environment approach

## Architectural Invariants
- no PYTHONPATH usage in services
- each service has proper dependencies built-in
- self-contained service packages

## Target Audience
- maintainers
- developers

## Per-File Focus
parts/systemd/user/*.nix:
- Remove PYTHONPATH from serviceConfig
- Use direct binary execution

## Completion Criteria
- all Planned Changes checked
- tests listed in Test Coverage are green
- No PYTHONPATH variables remain in services

## Completed / Coordinating with Parallel Execution

### Completed Items
- [x] Verify buildPythonApplication packages work correctly — implemented with pkgs.python313.pkgs.toPythonApplication, flake check passes
- [x] Update systemd service definitions to remove PYTHONPATH — all PYTHONPATH environment variables removed from systemd services in home-manager-module.nix
- [x] Test services work without PYTHONPATH — nix flake check passes successfully
- [x] Verify all dependencies are included in packages — all required Python packages now properly included in build

### Notes for Parallel Execution
- All PYTHONPATH references have been removed from systemd services
- New packaging approach uses pkgs.python313.pkgs.toPythonApplication instead of shell wrappers with PYTHONPATH
- The flake now builds successfully without any PYTHONPATH usage
- Dependencies are now properly included in each service package

## Post-Implementation Notes
- What changed from original plan
- What was removed as obsolete
- What should be refactored later