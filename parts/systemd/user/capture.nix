{ config, lib, pkgs, ... }:

let
  cfg = config.services.realtime-translator;
  capturePackage = cfg.package.capture or cfg.package;
in
{
  options = {
    services.realtime-translator.capture = {
      enable = lib.mkEnableOption "Real-time Translator Capture Service";
      package = lib.mkOption {
        type = lib.types.package;
        description = "Package for the capture service";
        default = capturePackage;
      };
    };
  };

  config = lib.mkIf cfg.capture.enable {
    systemd.user.services.rt-capture = {
      description = "Real-time Translator Capture Service";
      requires = [ "rt-capture.socket" ];
      after = [ "rt-capture.socket" ];
      wantedBy = [ "default.target" ];
      serviceConfig = {
        Type = "simple";
        ExecStart = "${capturePackage}/bin/translator-capture";
        Restart = "always";
        RestartSec = 5;
        Environment = [
          "PYTHONUNBUFFERED=1"
        ];
      };
    };

    systemd.user.sockets.rt-capture = {
      description = "Real-time Translator Capture Socket";
      wantedBy = [ "sockets.target" ];
      socketConfig = {
        ListenStream = "%t/rt-capture.sock";
        SocketMode = "0660";
      };
    };
  };
}