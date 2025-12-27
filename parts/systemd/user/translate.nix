{ config, lib, pkgs, ... }:

let
  cfg = config.services.realtime-translator;
  translatePackage = cfg.package.translate or cfg.package;
in
{
  options = {
    services.realtime-translator.translate = {
      enable = lib.mkEnableOption "Real-time Translator Translation Service";
      package = lib.mkOption {
        type = lib.types.package;
        description = "Package for the translation service";
        default = translatePackage;
      };
    };
  };

  config = lib.mkIf cfg.translate.enable {
    systemd.user.services.rt-translate = {
      description = "Real-time Translator Translation Service";
      requires = [ "rt-translate.socket" ];
      after = [ "rt-translate.socket" ];
      wantedBy = [ "default.target" ];
      serviceConfig = {
        Type = "simple";
        ExecStart = "${translatePackage}/bin/translator-translate";
        Restart = "always";
        RestartSec = 5;
        Environment = [
          "PYTHONUNBUFFERED=1"
        ];
      };
    };

    systemd.user.sockets.rt-translate = {
      description = "Real-time Translator Translation Socket";
      wantedBy = [ "sockets.target" ];
      socketConfig = {
        ListenStream = "%t/rt-translate.sock";
        SocketMode = "0660";
      };
    };
  };
}