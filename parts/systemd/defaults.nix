{ config, lib, pkgs, ... }:

let
  cfg = config.services.realtime-translator;
in
{
  options = {
    services.realtime-translator = {
      enable = lib.mkEnableOption "Real-time Translator services";
      
      package = lib.mkOption {
        type = lib.types.package;
        description = "The real-time translator package to use";
        default = pkgs.callPackage ../../default.nix {};
      };
    };
  };

  config = lib.mkIf cfg.enable {
    # Default configuration for all services
  };
}