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

  outputs = inputs@{ self, nixpkgs, flake-utils, home-manager, flake-parts, ... }:
    flake-parts.lib.mkFlake { inherit inputs; } {
      systems = [ "x86_64-linux" "aarch64-linux" "aarch64-darwin" "x86_64-darwin" ];

      perSystem = { config, self', inputs', pkgs, system, ... }:
        let
          lib = nixpkgs.lib;
          # Import production packages
          packages = (import ./prod/packages.nix { inherit pkgs lib; }).packages;
          
          # Import apps (using the packages we just defined)
          apps = (import ./prod/apps.nix { self = self'; packages = packages; }).apps;
        in
        {
          # Import production packages
          packages = packages;
          
          # Import development shell
          devShells = (import ./dev/devshell.nix { inherit pkgs; }).devShells;
          
          # Import apps
          apps = apps;
        };

      # Home Manager module
      flake = {
        homeManagerModules = {
          rt-translator = import ./home-manager-module.nix;
        };
      };
    };
}