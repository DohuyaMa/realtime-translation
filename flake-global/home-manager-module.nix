{ config, lib, pkgs, ... }:

let
  rtPackages = (import ./prod/packages.nix { inherit pkgs lib; }).packages;
  cfg = config.rt-translator;

  virtualSinksService = pkgs.writeShellApplication {
    name = "rt-virtual-sinks-service";
    runtimeInputs = [ pkgs.pipewire pkgs.pulseaudio ];
    text = ''
      pactl load-module module-null-sink sink_name=rt_virtual_input sink_properties=device.description="RT Virtual Input"
      pactl load-module module-null-sink sink_name=rt_virtual_output sink_properties=device.description="RT Virtual Output (Microphone)"
    '';
  };

in
{
  options.rt-translator = {
    enable = lib.mkEnableOption "Real-time speech translation system";

    whisper = {
      model = lib.mkOption {
        type    = lib.types.str;
        default = "medium";
        description = "Whisper model size (tiny/base/small/medium/large). UI can override via config file.";
      };
      device = lib.mkOption {
        type    = lib.types.str;
        default = "cuda";
        description = "Compute device: cuda or cpu.";
      };
      computeType = lib.mkOption {
        type    = lib.types.str;
        default = "float16";
        description = "Compute precision: float16, int8, int8_float16, etc.";
      };
      beamSize = lib.mkOption {
        type    = lib.types.int;
        default = 5;
        description = "Beam search width (higher = more accurate but slower).";
      };
      temperature = lib.mkOption {
        type    = lib.types.float;
        default = 0.0;
        description = "Sampling temperature. 0 = deterministic (greedy).";
      };
    };

    translate = {
      sourceLang = lib.mkOption {
        type    = lib.types.str;
        default = "uk";
        description = "Default source language code (ISO 639-1). UI can override.";
      };
      targetLang = lib.mkOption {
        type    = lib.types.str;
        default = "en";
        description = "Default target language code (ISO 639-1). UI can override.";
      };
      numBeams = lib.mkOption {
        type    = lib.types.int;
        default = 4;
        description = "Translation beam search width.";
      };
      repetitionPenalty = lib.mkOption {
        type    = lib.types.float;
        default = 1.2;
        description = "Repetition penalty for translation (1.0 = none).";
      };
      maxLength = lib.mkOption {
        type    = lib.types.int;
        default = 200;
        description = "Maximum output tokens per translation segment.";
      };
    };

    tts = {
      voice = lib.mkOption {
        type    = lib.types.str;
        default = "af_heart";
        description = "Kokoro TTS voice ID. UI can override.";
      };
      speed = lib.mkOption {
        type    = lib.types.float;
        default = 1.0;
        description = "TTS playback speed multiplier.";
      };
    };

    wyoming = {
      host = lib.mkOption {
        type    = lib.types.str;
        default = "localhost";
        description = "Wyoming faster-whisper service host.";
      };
      port = lib.mkOption {
        type    = lib.types.int;
        default = 10300;
        description = "Wyoming faster-whisper service port.";
      };
    };
  };

  config = lib.mkIf cfg.enable {
    home.packages = [
      rtPackages.app
      pkgs.pipewire
      pkgs.pulseaudio
      pkgs.espeak-ng  # misaki English G2P fallback for Kokoro TTS
    ];

    systemd.user.services = {
      "rt-capture" = {
        Unit = {
          Description = "RT-Capture-service";
          After   = [ "rt-capture.socket" ];
          Requires = [ "rt-capture.socket" ];
        };
        Service = {
          Type      = "simple";
          ExecStart = "${rtPackages.capture}/bin/translator-capture --socket-path %t/rt/rt-capture.sock";
          Restart   = "always";
          RestartSec = 5;
          Nice      = -5;
          IOSchedulingClass     = "best-effort";
          RuntimeDirectory      = "rt";
          RuntimeDirectoryPreserve = "yes";
        };
        Install = {
          WantedBy = [ "default.target" ];
          Also     = [ "rt-capture.socket" ];
        };
      };

      "rt-playback" = {
        Unit = {
          Description = "RT-Playback-service";
          After   = [ "rt-playback.socket" ];
          Requires = [ "rt-playback.socket" ];
        };
        Service = {
          Type      = "simple";
          ExecStart = "${rtPackages.playback}/bin/translator-playback --socket-path %t/rt/rt-playback.sock";
          Restart   = "always";
          RestartSec = 5;
          Nice      = -5;
          IOSchedulingClass     = "best-effort";
          RuntimeDirectory      = "rt";
          RuntimeDirectoryPreserve = "yes";
        };
        Install = {
          WantedBy = [ "default.target" ];
          Also     = [ "rt-playback.socket" ];
        };
      };

      "rt-translate" = {
        Unit = {
          Description = "RT-Translate-service";
          After   = [ "rt-translate.socket" ];
          Requires = [ "rt-translate.socket" ];
        };
        Service = {
          Type      = "simple";
          # CLI args = Nix-configured defaults.
          # Config file (~/.config/real-time-translator/config.yml) overrides at runtime.
          ExecStart = lib.concatStringsSep " " [
            "${rtPackages.translate}/bin/translator-translate"
            "--socket-path %t/rt/rt-translate.sock"
            "--source-lang ${cfg.translate.sourceLang}"
            "--target-lang ${cfg.translate.targetLang}"
            "--num-beams ${toString cfg.translate.numBeams}"
            "--repetition-penalty ${toString cfg.translate.repetitionPenalty}"
            "--max-length ${toString cfg.translate.maxLength}"
          ];
          Restart   = "always";
          RestartSec = 5;
          Nice      = -5;
          IOSchedulingClass     = "best-effort";
          RuntimeDirectory      = "rt";
          RuntimeDirectoryPreserve = "yes";
        };
        Install = {
          WantedBy = [ "default.target" ];
          Also     = [ "rt-translate.socket" ];
        };
      };

      "rt-tts" = {
        Unit = {
          Description = "RT-TTS-service";
          After   = [ "rt-tts.socket" ];
          Requires = [ "rt-tts.socket" ];
        };
        Service = {
          Type      = "simple";
          ExecStart = lib.concatStringsSep " " [
            "${rtPackages.tts}/bin/translator-tts"
            "--socket-path %t/rt/rt-tts.sock"
            "--voice ${cfg.tts.voice}"
            "--speed ${toString cfg.tts.speed}"
          ];
          Restart   = "always";
          RestartSec = 5;
          Nice      = -5;
          IOSchedulingClass     = "best-effort";
          RuntimeDirectory      = "rt";
          RuntimeDirectoryPreserve = "yes";
        };
        Install = {
          WantedBy = [ "default.target" ];
          Also     = [ "rt-tts.socket" ];
        };
      };

      "rt-whisper" = {
        Unit = {
          Description = "RT-Whisper-service";
          After   = [ "rt-whisper.socket" ];
          Requires = [ "rt-whisper.socket" ];
        };
        Service = {
          Type      = "simple";
          # CLI args = Nix-configured defaults.
          # Config file (~/.config/real-time-translator/config.yml) overrides at runtime.
          # Priority: config file (UI) > CLI arg (Nix) > hardcoded fallback in service.
          ExecStart = lib.concatStringsSep " " [
            "${rtPackages.whisper}/bin/translator-whisper"
            "--socket-path %t/rt/rt-whisper.sock"
            "--model ${cfg.whisper.model}"
            "--device ${cfg.whisper.device}"
            "--compute-type ${cfg.whisper.computeType}"
            "--beam-size ${toString cfg.whisper.beamSize}"
            "--temperature ${toString cfg.whisper.temperature}"
          ];
          Restart   = "always";
          RestartSec = 5;
          Nice      = -5;
          IOSchedulingClass     = "best-effort";
          RuntimeDirectory      = "rt";
          RuntimeDirectoryPreserve = "yes";
        };
        Install = {
          WantedBy = [ "default.target" ];
          Also     = [ "rt-whisper.socket" ];
        };
      };

      "rt-hybrid-whisper" = {
        Unit = {
          Description = "RT-Hybrid-Whisper-service (Wyoming Integration)";
          After   = [ "rt-hybrid-whisper.socket" "network.target" ];
          Requires = [ "rt-hybrid-whisper.socket" ];
        };
        Service = {
          Type      = "simple";
          ExecStart = lib.concatStringsSep " " [
            "${rtPackages."hybrid-whisper"}/bin/translator-hybrid-whisper"
            "--socket-path %t/rt/rt-hybrid-whisper.sock"
            "--model ${cfg.whisper.model}"
            "--device ${cfg.whisper.device}"
            "--compute-type ${cfg.whisper.computeType}"
            "--beam-size ${toString cfg.whisper.beamSize}"
            "--temperature ${toString cfg.whisper.temperature}"
            "--use-wyoming"
            "--wyoming-host ${cfg.wyoming.host}"
            "--wyoming-port ${toString cfg.wyoming.port}"
          ];
          Restart   = "always";
          RestartSec = 5;
          Nice      = -5;
          IOSchedulingClass     = "best-effort";
          Environment           = "PYTHONUNBUFFERED=1";
          RuntimeDirectory      = "rt";
          RuntimeDirectoryPreserve = "yes";
        };
        Install = {
          WantedBy = [ "default.target" ];
          Also     = [ "rt-hybrid-whisper.socket" ];
        };
      };

      "rt-app" = {
        Unit = {
          Description = "Real-time Translator Application";
          After   = [ "graphical-session.target" "pipewire.service" "rt-virtual-sinks.service" ];
          Requires = [ "rt-virtual-sinks.service" ];
          PartOf  = [ "graphical-session.target" ];
        };
        Service = {
          Type      = "simple";
          ExecStart = "${rtPackages."real-time-translator"}/bin/translator-ui";
          Restart   = "on-failure";
          RestartSec = 5;
          Environment    = "PATH=%h/.nix-profile/bin:/run/wrappers/bin:/etc/profiles/per-user/%u/bin:/nix/var/nix/profiles/default/bin:/run/current-system/sw/bin";
          PassEnvironment = "WAYLAND_DISPLAY DISPLAY XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS QT_QPA_PLATFORM";
        };
        Install = {
          WantedBy = [ "graphical-session.target" ];
        };
      };

      "rt-virtual-sinks" = {
        Unit = {
          Description = "Create RT Virtual Sinks";
          After = [ "pipewire.service" "pipewire-pulse.service" ];
          Wants = [ "pipewire.service" "pipewire-pulse.service" ];
        };
        Service = {
          Type            = "oneshot";
          ExecStart       = "${virtualSinksService}/bin/rt-virtual-sinks-service";
          RemainAfterExit = true;
        };
        Install = {
          WantedBy = [ "default.target" ];
        };
      };
    };

    xdg.configFile."pipewire/pipewire.conf.d/30-rt-virtual-sinks.conf".text = ''
      context.modules = [
        {
          name = libpipewire-module-null-sink
          args = {
            node.name = "rt_virtual_input"
            node.description = "RT-Virtual-Input"
            media.class = "Audio/Sink"
            stream.props = { audio.position = [ FL FR ]; }
          }
        }
        {
          name = libpipewire-module-null-sink
          args = {
            node.name = "rt_virtual_output"
            node.description = "RT-Virtual-Output"
            media.class = "Audio/Sink"
            stream.props = { audio.position = [ FL FR ]; }
          }
        }
      ]
    '';

    # Ensure the runtime directory exists before any socket unit tries to bind to it.
    systemd.user.tmpfiles.rules = [
      "d %t/rt 0700 - - -"
    ];

    systemd.user.sockets = {
      "rt-capture" = {
        Socket = {
          ListenStream = "%t/rt/rt-capture.sock";
          SocketMode   = "0660";
        };
        Unit.After = [ "rt-virtual-sinks.service" ];
        Install.WantedBy = [ "sockets.target" ];
      };

      "rt-playback" = {
        Socket = {
          ListenStream = "%t/rt/rt-playback.sock";
          SocketMode   = "0660";
        };
        Install.WantedBy = [ "sockets.target" ];
      };

      "rt-translate" = {
        Socket = {
          ListenStream = "%t/rt/rt-translate.sock";
          SocketMode   = "0660";
        };
        Install.WantedBy = [ "sockets.target" ];
      };

      "rt-tts" = {
        Socket = {
          ListenStream = "%t/rt/rt-tts.sock";
          SocketMode   = "0660";
        };
        Install.WantedBy = [ "sockets.target" ];
      };

      "rt-whisper" = {
        Socket = {
          ListenStream = "%t/rt/rt-whisper.sock";
          SocketMode   = "0660";
        };
        Install.WantedBy = [ "sockets.target" ];
      };

      "rt-hybrid-whisper" = {
        Socket = {
          ListenStream = "%t/rt/rt-hybrid-whisper.sock";
          SocketMode   = "0660";
        };
        Install.WantedBy = [ "sockets.target" ];
      };
    };
  };
}
