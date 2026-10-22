{ pkgs, lib ? pkgs.lib }:
let
  mkBundle = import ./speech-bundle.nix { inherit pkgs lib; };
  variants = service: names: lib.genAttrs names (variant: mkBundle { inherit service variant; });
in
{
  asr = variants "asr" [ "cpu" "gpu" "rocm" ];
  tts = variants "tts" [ "cpu" "gpu" ]; # no rocm: ORT ROCm unverified
  edge = pkgs.callPackage ./edge.nix { };
  inherit mkBundle;
  lib = import ../lib { inherit lib; };
}
