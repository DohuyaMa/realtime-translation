{ config, lib, pkgs, ... }:

let
  cfg = config.services.realtime-translator;
  hybridWhisperPackage = cfg.package.hybrid-whisper or cfg.package;
in
{
  options = {
    services.realtime-translator.hybrid-whisper = {
      enable = lib.mkEnableOption "Real-time Translator Hybrid Whisper Service";
      package = lib.mkOption {
        type = lib.types.package;
        description = "Package for the hybrid whisper service";
        default = hybridWhisperPackage;
      };
    };
  };

  config = lib.mkIf cfg.hybrid-whisper.enable {
    systemd.user.services.rt-hybrid-whisper = {
      description = "Real-time Translator Hybrid Whisper Service";
      requires = [ "rt-hybrid-whisper.socket" ];
      after = [ "rt-hybrid-whisper.socket" ];
      wantedBy = [ "default.target" ];
      serviceConfig = {
        Type = "simple";
        ExecStart = "${hybridWhisperPackage}/bin/translator-hybrid-whisper";
        Restart = "always";
        RestartSec = 5;
        Environment = [
          "PYTHONUNBUFFERED=1"
        ];
      };
    };

    systemd.user.sockets.rt-hybrid-whisper = {
      description = "Real-time Translator Hybrid Whisper Socket";
      wantedBy = [ "sockets.target" ];
      socketConfig = {
        ListenStream = "%t/rt-hybrid-whisper.sock";
        SocketMode = "0660";
      };
    };
  };
}