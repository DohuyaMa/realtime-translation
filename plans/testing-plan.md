# Testing Plan for Systemd Update

## Purpose
Create comprehensive testing strategy to ensure the new systemd architecture works correctly with all components properly integrated.

## Scope
This plan covers unit, integration, and system testing for the new architecture. It ensures all components work together after the migration from hardcoded paths and monolithic environment to modular, properly packaged services.

## Entry Points
- tests/systemd-update/build-test.nix
- tests/systemd-update/runtime-test.py
- tests/systemd-update/socket-test.py
- tests/unit/
- tests/integration/
- tests/systemd/

## Planned Changes
- [ ] Create build tests for all new packages
- [ ] Create runtime configuration tests
- [ ] Create socket communication tests
- [ ] Create integration tests for full pipeline

## Test Coverage
- Verify each buildPythonApplication builds → nix flake check
- Verify runtime config creates proper paths → unit test
- Verify socket communication works → integration test
- Verify full pipeline functionality → integration test
- Verify systemd service activation → integration test

## Dependencies
### Hard Dependencies
- blocks: all other plan files (testing validates all changes)

### Soft Dependencies
- should be reviewed after: all other plan files are implemented

## Obsolete / To Be Removed
- Old testing approaches that don't match new architecture
- Tests that rely on hardcoded paths

## Architectural Invariants
- tests validate new architecture
- no hardcoded /tmp paths in tests
- tests work with new XDG-compliant paths

## Target Audience
- developers
- maintainers

## Per-File Focus
tests/systemd-update/build-test.nix:
- Build validation for packages

tests/systemd-update/runtime-test.py:
- Runtime configuration validation

tests/systemd-update/socket-test.py:
- Socket communication validation

## Completion Criteria
- all Planned Changes checked
- all tests pass
- test coverage includes all architectural changes

## Post-Implementation Notes
- What changed from original plan
- What was removed as obsolete
- What should be refactored later