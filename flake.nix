{
  description = "Real-time speech translation system";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";
    flake-utils.url = "github:numtide/flake-utils";
    home-manager = {
      url = "github:nix-community/home-manager";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    flake-global = {
      url = "path:./flake-global";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.home-manager.follows = "home-manager";
    };
  };

  outputs = { self, nixpkgs, flake-utils, home-manager, flake-global }:
    flake-utils.lib.eachSystem ["x86_64-linux"] (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        flakeGlobal = flake-global.legacyPackages.${system};
      in
      {
        # Import packages from the modular flake
        packages = flakeGlobal.packages or {};
        
        # Import devShells from the modular flake
        devShells = flakeGlobal.devShells or {};
        
        # Import apps from the modular flake
        apps = flakeGlobal.apps or {};
      }
    ) // {
      # Home Manager module
      homeManagerModules.rt-translator = flake-global.homeManagerModules.rt-translator;

      # NixOS module for production deployment
      nixosModules.virtual-sinks = import ./nixosModules/virtual-sinks.nix;
    };
}
