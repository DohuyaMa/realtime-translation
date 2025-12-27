# Flake Debug Queries for REPL Inspection

This document provides a comprehensive list of typical REPL queries for inspecting the flake-parts configuration of the real-time translation system. These queries are useful for manual inspection during development and debugging.

## Prerequisites

To use these queries, first enable debug mode in your flake:

```nix
{
  debug = true;
  systems = [ "x86_64-linux" ];
  # ... other configuration
}
```

Then load the flake in nix repl:

```bash
nix repl
nix-repl> :lf .
```

## System Configuration Queries

### Inspect the perSystem configuration for your machine
```nix
currentSystem.allModuleArgs.pkgs.stdenv.hostPlatform.system
```

### Inspect the perSystem configuration for a different system type
```nix
debug.allSystems.armv7l-linux.allModuleArgs.pkgs.stdenv.hostPlatform.system
```

### Inspect top-level systems
```nix
debug.systems
```

## Package and Derivation Queries

### Check available packages
```nix
builtins.attrNames currentSystem.packages
```

### Inspect a specific package
```nix
currentSystem.packages.capture
```

### Check all available packages for a system
```nix
builtins.attrNames (currentSystem.packages or {})
```

## Option Inspection Queries

### Inspect a top-level option
```nix
debug.options.packages
```

### Where is a per system value defined?
```nix
currentSystem.options.pre-commit.settings.files
```

### Where is a top-level value defined?
```nix
debug.options.system.files
```

### Where is a top-level option declared?
```nix
debug.options.systems.declarations
```

## Service and Module Queries

### Inspect systemd services
```nix
debug.options.nixosModules or {}
```

### Check available NixOS modules
```nix
builtins.attrNames (self.nixosModules or {})
```

### Inspect home-manager modules
```nix
builtins.attrNames (self.homeManagerModules or {})
```

## Flake Inputs Queries

### Check all flake inputs
```nix
builtins.attrNames self.inputs
```

### Inspect a specific input
```nix
self.inputs.nixpkgs
```

## Development Shell Queries

### Check available devShells
```nix
builtins.attrNames currentSystem.devShells
```

### Inspect the default devShell
```nix
currentSystem.devShells.default
```

## App Queries

### Check available apps
```nix
builtins.attrNames currentSystem.apps
```

### Inspect a specific app
```nix
currentSystem.apps.default or {}
```

## Testing Queries

### Check available tests
```nix
builtins.attrNames (self.tests or {})
```

### Inspect a specific test
```nix
self.tests."all python services are defined" or {}
```

## Debugging Tips

1. Use `builtins.deepSeq <expr> "done"` to force evaluation of expressions that might be lazy
2. Use `:p <expr>` in nix-repl to print values with unlimited depth
3. Use `builtins.typeOf` to check the type of an expression
4. Use `builtins.isAttrs`, `builtins.isList`, etc. to check types programmatically

## Troubleshooting Common Issues

### If a package doesn't exist:
```nix
# Check if the package exists
hasAttr "capture" currentSystem.packages
# List all available packages
builtins.attrNames currentSystem.packages
```

### If an option is not taking effect:
```nix
# Check where the option value comes from
debug.options.<option-path>.files
debug.options.<option-path>.declarations
```

These queries provide comprehensive inspection capabilities for the flake-parts configuration and are essential for debugging and validating the flake structure.