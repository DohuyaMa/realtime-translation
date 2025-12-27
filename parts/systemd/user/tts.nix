{ config, lib, pkgs, ... }:

let
  cfg = config.services.realtime-translator;
  ttsPackage = cfg.package.tts or cfg.package;
in
{
  options = {
    services.realtime-translator.tts = {
      enable = lib.mkEnableOption "Real-time Translator TTS Service";
      package = lib.mkOption {
        type = lib.types.package;
        description = "Package for the TTS service";
        default = ttsPackage;
      };
    };
  };

  config = lib.mkIf cfg.tts.enable {
    systemd.user.services.rt-tts = {
      description = "Real-time Translator TTS Service";
      requires = [ "rt-tts.socket" ];
      after = [ "rt-tts.socket" ];
      wantedBy = [ "default.target" ];
      serviceConfig = {
        Type = "simple";
        ExecStart = "${ttsPackage}/bin/translator-tts";
        Restart = "always";
        RestartSec = 5;
        Environment = [
          "PYTHONUNBUFFERED=1"
        ];
      };
    };

    systemd.user.sockets.rt-tts = {
      description = "Real-time Translator TTS Socket";
      wantedBy = [ "sockets.target" ];
      socketConfig = {
        ListenStream = "%t/rt-tts.sock";
        SocketMode = "0660";
      };
    };
  };
}