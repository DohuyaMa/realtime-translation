{
  description = "Real-time speech translation system";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    flake-utils.url = "github:numtide/flake-utils";
    home-manager = {
      url = "github:nix-community/home-manager";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    flake-parts = {
      url = "github:hercules-ci/flake-parts";
      inputs.nixpkgs-lib.follows = "nixpkgs";
    };
    nix-unit = {
      url = "github:nix-community/nix-unit";
    };
    flake-global.url = "path:./flake-global";
  };
  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
      home-manager,
      flake-parts,
      nix-unit,
      flake-global,
      ...
    }:
    let
      systems = [ "x86_64-linux" ];
    in
    flake-utils.lib.eachSystem systems (system: {
      # Делегуємо всі пакети, devShells та apps на flake-global
      packages = flake-global.packages.${system};
      devShells = flake-global.devShells.${system};
      apps = flake-global.apps.${system};
    })
    // {
      # Home Manager модуль
      homeManagerModules.rt-translator = flake-global.homeManagerModules.rt-translator;

      # NixOS модулі (можна додати додаткові тут)
      nixosModules.virtual-sinks = import ./nixosModules/virtual-sinks.nix;

      # Nix-unit тести
      tests = {
        "all python services are defined" = {
          expr = builtins.attrNames (self.packages.x86_64-linux or { });
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

        "systemd services exist" = {
          expr = builtins.attrNames (self.nixosModules or { });
          expected = [
            "virtual-sinks"
          ];
        };

        "flake inputs are properly defined" = {
          expr = builtins.attrNames self.inputs;
          expected = [
            "nixpkgs"
            "flake-utils"
            "home-manager"
            "flake-parts"
            "nix-unit"
            "flake-global"
          ];
        };
      };
    };
}
