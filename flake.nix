{
  description = "Real-time speech translation: edge UI + ASR/TTS services";

  # HINT only: CUDA variants (rt-*-gpu) are substitutable from cache.nixos-cuda.org.
  # Untrusted users' flake nixConfig is ignored — CUDA hosts get it via system
  # nix.settings in the infra. Never relied upon for correctness.
  nixConfig = {
    extra-substituters = [ "https://cache.nixos-cuda.org" ];
    extra-trusted-public-keys = [ "cache.nixos-cuda.org:74DUi4Ye579gUqzH4ziL9IyiJBlDpMRn9MBN8oNan9M=" ];
  };

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    home-manager = {
      url = "github:nix-community/home-manager";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, home-manager, ... }:
    let
      lib = nixpkgs.lib;
      systems = [ "x86_64-linux" ];
      pkgsFor = system: import nixpkgs { inherit system; config.allowUnfree = true; };
      forAllSystems = f: lib.genAttrs systems (system: f (pkgsFor system));
      rtLib = import ./nix/lib { inherit lib; };
    in
    {
      lib.rtSpeech = rtLib;

      nixosModules.virtual-sinks = import ./nix/nixos/virtual-sinks.nix;

      devShells = forAllSystems (pkgs: { default = import ./nix/devshell.nix { inherit pkgs; }; });

      checks = forAllSystems (pkgs: {
        flake-purity = import ./nix/checks/flake-purity.nix { inherit pkgs; src = self; };
        env-lines = import ./nix/checks/env-lines.nix { inherit pkgs rtLib; };
      });
    };
}
