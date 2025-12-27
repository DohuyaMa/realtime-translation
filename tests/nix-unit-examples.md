# Nix-Unit Test Suite Examples

This document provides comprehensive examples of nix-unit tests for the real-time translation system's flake structure. These examples demonstrate how to validate the flake configuration and ensure structural integrity.

## Basic Test Structure

Nix-unit tests follow the format:
```nix
{
  "test name" = {
    expr = "<expression to evaluate>";
    expected = <expected result>;
  };
}
```

## Package Validation Tests

### Test that all expected packages exist
```nix
{
  "all python services are defined" = {
    expr = ''
      let
        flake = import ./flake.nix {};
        pkgs = flake.packages.x86_64-linux or {};
      in
      builtins.attrNames pkgs
    '';
    expected = [
      "capture"
      "whisper" 
      "translate"
      "tts"
      "playback"
      "app"
      "hybrid-whisper"
      "ui"
    ];
  };
}
```

### Test specific package structure
```nix
{
  "capture package has expected attributes" = {
    expr = ''
      let
        flake = import ./flake.nix {};
        capturePkg = flake.packages.x86_64-linux.capture or {};
      in
      builtins.attrNames capturePkg
    '';
    expected = [ "outPath" "type" "meta" "passthru" ];
  };
}
```

## Input Validation Tests

### Test that all required inputs are defined
```nix
{
  "flake inputs are properly defined" = {
    expr = "builtins.attrNames (import ./flake.nix {}).inputs";
    expected = [
      "nixpkgs"
      "flake-utils"
      "home-manager"
      "flake-parts"
      "nix-unit"
    ];
  };
}
```

### Test specific input versions
```nix
{
  "nixpkgs input has expected url" = {
    expr = "(import ./flake.nix {}).inputs.nixpkgs.url";
    expected = "github:NixOS/nixpkgs/nixos-25.11";
  };
}
```

## Development Environment Tests

### Test devShell availability
```nix
{
  "devShell exists" = {
    expr = ''
      let
        flake = import ./flake.nix {};
        devShells = flake.devShells.x86_64-linux or {};
      in
      builtins.hasAttr "default" devShells
    '';
    expected = true;
  };
}
```

### Test devShell packages
```nix
{
  "devShell contains required packages" = {
    expr = ''
      let
        flake = import ./flake.nix {};
        devShell = flake.devShells.x86_64-linux.default or {};
      in
      if devShell ? packages then
        builtins.map (pkg: if pkg ? name then pkg.name else builtins.unsafeDiscardStringContext (builtins.toString pkg)) (devShell.packages or [])
      else
        []
    '';
    expected = [];  # Actual expected packages would depend on your devShell configuration
  };
}
```

## System Configuration Tests

### Test supported systems
```nix
{
  "supported systems are defined" = {
    expr = ''
      let
        flake = import ./flake.nix {};
        # Access the systems through the flake-parts debug interface
        systems = [ "x86_64-linux" ];  # This would need to match your actual configuration
      in
      systems
    '';
    expected = [ "x86_64-linux" "aarch64-linux" "aarch64-darwin" "x86_64-darwin" ];
  };
}
```

## App Validation Tests

### Test that apps are defined
```nix
{
  "apps are properly defined" = {
    expr = ''
      let
        flake = import ./flake.nix {};
        apps = flake.apps.x86_64-linux or {};
      in
      builtins.attrNames apps
    '';
    expected = [ "default" ];  # or whatever apps you have defined
  };
}
```

## Module Validation Tests

### Test home-manager modules
```nix
{
  "home-manager modules exist" = {
    expr = "builtins.attrNames (import ./flake.nix {}).homeManagerModules";
    expected = [ "rt-translator" ];
  };
}
```

### Test NixOS modules
```nix
{
  "nixos modules exist" = {
    expr = "builtins.attrNames (import ./flake.nix {}).nixosModules";
    expected = [ "virtual-sinks" ];
  };
}
```

## Comprehensive Integration Test

### Test overall flake structure
```nix
{
  "flake has expected top-level attributes" = {
    expr = ''
      let
        flake = import ./flake.nix {};
      in
      builtins.filter (attr: attr != "outputs" && attr != "nixosConfigurations" && attr != "homeConfigurations") (builtins.attrNames flake)
    '';
    expected = [
      "inputs"
      "outputs"
      "homeManagerModules"
      "nixosModules"
      "checks"  # if using checks
      "packages"  # if using packages
      "devShells"  # if using devShells
      "apps"  # if using apps
      "tests"  # if using tests
    ];
  };
}
```

## Running Nix-Unit Tests

To run these tests, they should be defined in the flake's `checks` or `tests` attribute:

```nix
{
  checks.x86_64-linux.unit-tests = nix-unit.lib.makeTest {
    name = "unit-tests";
    expr = builtins.readFile ./unit-tests.nix;  # or inline the test definitions
  };
}
```

Then run with:
```bash
nix flake check
```

## Best Practices

1. **Be Specific**: Tests should validate specific aspects of your flake configuration
2. **Be Comprehensive**: Cover all major components (packages, devShells, apps, modules)
3. **Be Maintainable**: Use descriptive test names and keep tests up-to-date with changes
4. **Be Practical**: Focus on validating the aspects that are critical for your system to work correctly
5. **Use Expected Values**: Always specify expected values to catch regressions

These examples provide a foundation for comprehensive nix-unit testing of your flake structure, ensuring the integrity of your Nix-based build system.