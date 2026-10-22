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
      rtPackagesFor = pkgs: import ./nix/packages { inherit pkgs; };
      flattenRt = rt: lib.filterAttrs (_: drv: !(drv.meta.broken or false)) (lib.listToAttrs (lib.concatMap
        (service: lib.mapAttrsToList (variant: drv: lib.nameValuePair "rt-${service}-${variant}" drv) rt.${service})
        [ "asr" "tts" ]));
    in
    {
      lib.rtSpeech = rtLib;

      overlays.default = final: _prev: { rtSpeech = import ./nix/packages { pkgs = final; }; };
      packages = forAllSystems (pkgs:
        let rt = rtPackagesFor pkgs; in
        flattenRt rt // { rt-edge = rt.edge; default = rt.edge; });
      apps = forAllSystems (pkgs: {
        default = { type = "app"; program = lib.getExe (rtPackagesFor pkgs).edge; };
      });

      homeManagerModules.rt-translator = import ./nix/home-manager/rt-translator.nix;
      homeManagerModules.default = self.homeManagerModules.rt-translator;

      nixosModules.rt-speech = import ./nix/nixos/rt-speech.nix;
      nixosModules.default = self.nixosModules.rt-speech;
      nixosModules.virtual-sinks = import ./nix/nixos/virtual-sinks.nix;

      devShells = forAllSystems (pkgs: { default = import ./nix/devshell.nix { inherit pkgs; }; });

      checks = forAllSystems (pkgs: {
        flake-purity = import ./nix/checks/flake-purity.nix { inherit pkgs; src = self; };
        nixos-eval-gpu = import ./nix/checks/nixos-eval-gpu.nix { inherit pkgs self nixpkgs; };
        env-lines = import ./nix/checks/env-lines.nix { inherit pkgs rtLib; };
        nixos-rt-speech = import ./nix/checks/nixos-rt-speech.nix { inherit pkgs self; };
        overlay = import ./nix/checks/overlay.nix { inherit pkgs self; };
        edge-pytest = import ./nix/checks/edge-pytest.nix { inherit pkgs; };
        hm-edge = import ./nix/checks/hm-edge.nix { inherit pkgs self home-manager; };
        rt-edge = (rtPackagesFor pkgs).edge;
        rt-asr-cpu = (rtPackagesFor pkgs).asr.cpu;
        rt-tts-cpu = (rtPackagesFor pkgs).tts.cpu;
      });
    };
}
