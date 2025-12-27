{ config, lib, ... }: 

let
  cfg = config.rt.runtime;
  socketDir = cfg.socketDir;
in
{
  options.rt.runtime.sockets = {
    capture = lib.mkOption {
      type = lib.types.str;
      default = "${socketDir}/rt-capture.sock";
      description = "Socket path for capture service";
    };
    
    playback = lib.mkOption {
      type = lib.types.str;
      default = "${socketDir}/rt-playback.sock";
      description = "Socket path for playback service";
    };
    
    tts = lib.mkOption {
      type = lib.types.str;
      default = "${socketDir}/rt-tts.sock";
      description = "Socket path for TTS service";
    };
    
    translate = lib.mkOption {
      type = lib.types.str;
      default = "${socketDir}/rt-translate.sock";
      description = "Socket path for translation service";
    };
    
    whisper = lib.mkOption {
      type = lib.types.str;
      default = "${socketDir}/rt-whisper.sock";
      description = "Socket path for whisper service";
    };
    
    "hybrid-whisper" = lib.mkOption {
      type = lib.types.str;
      default = "${socketDir}/rt-hybrid-whisper.sock";
      description = "Socket path for hybrid whisper service";
    };
  };

  config = {
    # Define default socket paths
    rt.runtime.sockets = {
      capture = lib.mkDefault "${cfg.socketDir}/rt-capture.sock";
      playback = lib.mkDefault "${cfg.socketDir}/rt-playback.sock";
      tts = lib.mkDefault "${cfg.socketDir}/rt-tts.sock";
      translate = lib.mkDefault "${cfg.socketDir}/rt-translate.sock";
      whisper = lib.mkDefault "${cfg.socketDir}/rt-whisper.sock";
      "hybrid-whisper" = lib.mkDefault "${cfg.socketDir}/rt-hybrid-whisper.sock";
    };
  };
}