{ config, lib, ... }: 

let
  cfg = config.rt.runtime;
  socketDir = cfg.socketDir;
in
{
  options.rt.runtime.systemd = {
    enable = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Enable systemd socket configurations for the runtime paths";
    };
  };

  config = lib.mkIf cfg.enable {
    # Systemd socket configurations that use the runtime paths
    systemd.sockets = {
      "rt-capture" = {
        description = "Real-time Translator Capture Socket";
        wantedBy = [ "sockets.target" ];
        socketConfig = {
          listenStreams = [ "${socketDir}/rt-capture.sock" ];
          socketMode = "0660";
        };
      };
      
      "rt-playback" = {
        description = "Real-time Translator Playback Socket";
        wantedBy = [ "sockets.target" ];
        socketConfig = {
          listenStreams = [ "${socketDir}/rt-playback.sock" ];
          socketMode = "0660";
        };
      };
      
      "rt-tts" = {
        description = "Real-time Translator TTS Socket";
        wantedBy = [ "sockets.target" ];
        socketConfig = {
          listenStreams = [ "${socketDir}/rt-tts.sock" ];
          socketMode = "0660";
        };
      };
      
      "rt-translate" = {
        description = "Real-time Translator Translation Socket";
        wantedBy = [ "sockets.target" ];
        socketConfig = {
          listenStreams = [ "${socketDir}/rt-translate.sock" ];
          socketMode = "0660";
        };
      };
      
      "rt-whisper" = {
        description = "Real-time Translator Whisper Socket";
        wantedBy = [ "sockets.target" ];
        socketConfig = {
          listenStreams = [ "${socketDir}/rt-whisper.sock" ];
          socketMode = "0660";
        };
      };
      
      "rt-hybrid-whisper" = {
        description = "Real-time Translator Hybrid Whisper Socket";
        wantedBy = [ "sockets.target" ];
        socketConfig = {
          listenStreams = [ "${socketDir}/rt-hybrid-whisper.sock" ];
          socketMode = "0660";
        };
      };
    };
  };
}