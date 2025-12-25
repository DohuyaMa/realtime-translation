{
  description = "Modular Real-time speech translation system";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";
    flake-utils.url = "github:numtide/flake-utils";
    home-manager = {
      url = "github:nix-community/home-manager";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    flake-parts = {
      url = "github:hercules-ci/flake-parts";
      inputs.nixpkgs-lib.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, flake-utils, home-manager, flake-parts }:
    flake-parts.lib.mkFlake { inherit inputs; } {
      systems = [ "x86_64-linux" "aarch64-linux" "aarch64-darwin" "x86_64-darwin" ];

      perSystem = { config, self', inputs', pkgs, system, ... }: {
        # Import production packages
        packages = (import ./prod/packages.nix { inherit pkgs; }).packages;
        
        # Import development shell
        devShells = (import ./dev/devshell.nix { inherit pkgs; }).devShells;
        
        # Import apps
        apps = (import ./prod/apps.nix { self = self'; }).apps;
      };

      # Home Manager module
      flake = {
        homeManagerModules = {
          rt-translator = import ./home-manager-module.nix;
        };

        # NixOS module for production deployment
        nixosModules = {
          virtual-sinks = import ../nixosModules/virtual-sinks.nix;
        };
      };
    };
}