# Package set per bundle variant. The consumer's pkgs is NEVER modified.
#  cpu  - consumer pkgs as is.
#  gpu  - separate instance with global cudaSupport and DEFAULT cudaCapabilities
#         (hashes match cache.nixos-cuda.org). Never torch -> torch-bin.
#  rocm - ctranslate2 via nixpkgs' rocmSupport, scoped packageOverrides.
{ pkgs, lib, variant, rocmGpuTargets }:
if variant == "cpu" then { inherit pkgs; python = pkgs.python313; }
else if variant == "gpu" then
  let
    cudaPkgs = import pkgs.path {
      system = pkgs.stdenv.hostPlatform.system;
      config = { allowUnfree = true; cudaSupport = true; };
    };
  in
  { pkgs = cudaPkgs; python = cudaPkgs.python313; }
else if variant == "rocm" then
  let
    ct2Rocm = pkgs.ctranslate2.override { rocmSupport = true; inherit rocmGpuTargets; };
    python = pkgs.python313.override {
      self = python;
      packageOverrides = _: super: {
        ctranslate2 = super.ctranslate2.override { ctranslate2-cpp = ct2Rocm; };
      };
    };
  in
  { inherit pkgs python; }
else throw "rtSpeech: unknown variant '${variant}'"
