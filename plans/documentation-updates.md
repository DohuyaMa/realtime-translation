# Documentation Updates Plan

## Purpose
Update documentation to reflect the new systemd architecture with flake-parts structure, eliminating references to the old monolithic approach.

## Scope
This plan updates all documentation files to reflect the new modular architecture, including architectural docs, development docs, and configuration docs. It ensures documentation matches the implemented architecture.

## Entry Points
- docs/flake-architecture.md
- docs/flake-modular-structure.md
- docs/refactored-architecture.md
- CONTRIBUTING.md
- RUNNING_AND_TESTING.md
- TESTING_INSTRUCTIONS.md
- README.md
- README_NIX.md
- context/systemd-update.md
- context/python-packaging-strategy.md
- context/runtime-configuration.md
- context/flake-parts-structure.md
- MIGRATION_GUIDE.md
- plans/implementation_plan.md

## Planned Changes
- [ ] Update architecture documentation to reflect new structure
- [ ] Update development workflow documentation
- [ ] Create migration guide for users
- [ ] Update testing documentation to match new approach

## Test Coverage
- Verify documentation references new paths → documentation check
- Verify no references to old architecture remain → documentation check
- Verify examples match new implementation → documentation check

## Dependencies
### Hard Dependencies
- all other plan files (documentation reflects all changes made)

### Soft Dependencies
- should be reviewed after: all implementation is complete

## Obsolete / To Be Removed
- References to hardcoded /tmp paths
- References to monolithic python environment
- References to shell script wrappers
- Old architecture diagrams and descriptions

## Architectural Invariants
- documentation matches implemented architecture
- examples use new patterns
- migration path is clear for existing users

## Target Audience
- users
- developers
- maintainers

## Per-File Focus
docs/flake-architecture.md:
- New flake-parts structure
- Modular service definitions

CONTRIBUTING.md:
- New development workflow
- How to add new services

MIGRATION_GUIDE.md:
- Path from old to new architecture
- Breaking changes and adaptations

## Completion Criteria
- all Planned Changes checked
- documentation matches implementation
- obsolete references removed

## Post-Implementation Notes
- What changed from original plan
- What was removed as obsolete
- What should be refactored later