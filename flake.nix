{
  description = "Real-time speech translation system";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";
    flake-utils.url = "github:numtide/flake-utils";
    home-manager = {
      url = "github:nix-community/home-manager";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, flake-utils, home-manager, ... }:
    flake-utils.lib.eachSystem [ "x86_64-linux" "aarch64-linux" "aarch64-darwin" "x86_64-darwin" ] (system:
      let
        pkgs = import nixpkgs {
          inherit system;
          config.allowUnfree = true;
        };
        lib = nixpkgs.lib;
        packages = (import ./flake-global/prod/packages.nix { inherit pkgs lib; }).packages;
      in {
        inherit packages;
        devShells = (import ./flake-global/dev/devshell.nix { inherit pkgs; }).devShells;
        apps.default = {
          type = "app";
          program = "${packages.default}/bin/translator-ui";
        };
      }
    ) // {
      homeManagerModules.rt-translator = import ./flake-global/home-manager-module.nix;
      nixosModules.virtual-sinks = import ./nixosModules/virtual-sinks.nix;
    };
}
