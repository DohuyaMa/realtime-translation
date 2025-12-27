{ config, lib, pkgs, ... }:

let
  cfg = config.services.realtime-translator;
in
{
  options = {
    services.realtime-translator.virtual-sinks = {
      enable = lib.mkEnableOption "Real-time Translator Virtual Sinks Service";
    };
  };

  config = lib.mkIf cfg.virtual-sinks.enable {
    systemd.user.services.rt-virtual-sinks = {
      description = "Real-time Translator Virtual Sinks Service";
      wantedBy = [ "default.target" ];
      serviceConfig = {
        Type = "simple";
        ExecStart = "${pkgs.pipewire}/bin/pipewire";
        Restart = "always";
        RestartSec = 5;
      };
    };
  };
}