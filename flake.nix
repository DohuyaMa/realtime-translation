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
        # Access packages from flake-global which uses flake-parts
        # The flake-global uses flake-parts, so outputs are in perSystem
        flakeGlobalPerSystem = flake-global.legacyPackages.${system} or flake-global.perSystem.${system} or {};
      in
      {
        # Import packages from the modular flake
        packages = flakeGlobalPerSystem.packages or {};
        
        # Import devShells from the modular flake
        devShells = flakeGlobalPerSystem.devShells or {};
        
        # Import apps from the modular flake
        apps = flakeGlobalPerSystem.apps or {};
      }
    ) // {
      # Home Manager module
      homeManagerModules.rt-translator = flake-global.homeManagerModules.rt-translator;

      # NixOS module for production deployment
      nixosModules.virtual-sinks = import ./nixosModules/virtual-sinks.nix;
    };
}
