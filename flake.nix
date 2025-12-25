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

  outputs = { self, nixpkgs, flake-utils, home-manager }:
    let
      # Import modular configurations directly
      rtTranslatorModule = import ./flake-global/home-manager-module.nix;
      
      # Import production packages
      prodPackages = import ./flake-global/prod/packages.nix;
      
      # Import development shell
      devShellConfig = import ./flake-global/dev/devshell.nix;
      
      # Import apps
      appsConfig = import ./flake-global/prod/apps.nix;
    in
    flake-utils.lib.eachSystem ["x86_64-linux"] (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        # Get packages for this system
        systemProdPackages = prodPackages { inherit pkgs; };
        # Get devShell for this system
        systemDevShell = devShellConfig { inherit pkgs; };
        # Get apps for this system
        systemApps = appsConfig { packages = systemProdPackages.packages; };
      in
      {
        # Import packages from the modular configuration
        packages = systemProdPackages.packages;
        
        # Import devShells from the modular configuration
        devShells = systemDevShell.devShells;
        
        # Import apps from the modular configuration
        apps = systemApps.apps;
      }
    ) // {
      # Home Manager module
      homeManagerModules.rt-translator = rtTranslatorModule;

      # NixOS module for production deployment
      nixosModules.virtual-sinks = import ./nixosModules/virtual-sinks.nix;
    };
}
