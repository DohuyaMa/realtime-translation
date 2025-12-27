{ config, lib, pkgs, ... }:

let
  cfg = config.services.realtime-translator;
  playbackPackage = cfg.package.playback or cfg.package;
in
{
  options = {
    services.realtime-translator.playback = {
      enable = lib.mkEnableOption "Real-time Translator Playback Service";
      package = lib.mkOption {
        type = lib.types.package;
        description = "Package for the playback service";
        default = playbackPackage;
      };
    };
  };

  config = lib.mkIf cfg.playback.enable {
    systemd.user.services.rt-playback = {
      description = "Real-time Translator Playback Service";
      requires = [ "rt-playback.socket" ];
      after = [ "rt-playback.socket" ];
      wantedBy = [ "default.target" ];
      serviceConfig = {
        Type = "simple";
        ExecStart = "${playbackPackage}/bin/translator-playback";
        Restart = "always";
        RestartSec = 5;
        Environment = [
          "PYTHONUNBUFFERED=1"
        ];
      };
    };

    systemd.user.sockets.rt-playback = {
      description = "Real-time Translator Playback Socket";
      wantedBy = [ "sockets.target" ];
      socketConfig = {
        ListenStream = "%t/rt-playback.sock";
        SocketMode = "0660";
      };
    };
  };
}