{ config, lib, pkgs, ... }:

let
  cfg = config.services.realtime-translator;
  appPackage = cfg.package.ui or cfg.package;
in
{
  options = {
    services.realtime-translator.app = {
      enable = lib.mkEnableOption "Real-time Translator App Service";
      package = lib.mkOption {
        type = lib.types.package;
        description = "Package for the main app service";
        default = appPackage;
      };
    };
  };

  config = lib.mkIf cfg.app.enable {
    systemd.user.services.rt-app = {
      description = "Real-time Translator App Service";
      wantedBy = [ "default.target" ];
      serviceConfig = {
        Type = "simple";
        ExecStart = "${appPackage}/bin/translator-ui";
        Restart = "always";
        RestartSec = 5;
        Environment = [
          "PYTHONUNBUFFERED=1"
        ];
      };
    };
  };
}