{ config, lib, pkgs, ... }:

let
  cfg = config.services.realtime-translator;
  whisperPackage = cfg.package.whisper or cfg.package;
in
{
  options = {
    services.realtime-translator.whisper = {
      enable = lib.mkEnableOption "Real-time Translator Whisper Service";
      package = lib.mkOption {
        type = lib.types.package;
        description = "Package for the whisper service";
        default = whisperPackage;
      };
    };
  };

  config = lib.mkIf cfg.whisper.enable {
    systemd.user.services.rt-whisper = {
      description = "Real-time Translator Whisper Service";
      requires = [ "rt-whisper.socket" ];
      after = [ "rt-whisper.socket" ];
      wantedBy = [ "default.target" ];
      serviceConfig = {
        Type = "simple";
        ExecStart = "${whisperPackage}/bin/translator-whisper";
        Restart = "always";
        RestartSec = 5;
        Environment = [
          "PYTHONUNBUFFERED=1"
        ];
      };
    };

    systemd.user.sockets.rt-whisper = {
      description = "Real-time Translator Whisper Socket";
      wantedBy = [ "sockets.target" ];
      socketConfig = {
        ListenStream = "%t/rt-whisper.sock";
        SocketMode = "0660";
      };
    };
  };
}