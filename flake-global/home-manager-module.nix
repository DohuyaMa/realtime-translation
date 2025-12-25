{ config, pkgs, ... }: 

let
  # Python packages
  pythonPackages = pkgs.python313Packages;
  kokoroPackage = pythonPackages.kokoro;
  
  # Create proper Python environment for runtime
  pythonEnv = pkgs.python313.withPackages (ps: with ps; [
    # Core dependencies
    pyaudio
    numpy
    sounddevice
    
    # AI and ML
    torch
    transformers
    faster-whisper
    ctranslate2
    onnxruntime
    pyside6
    
    # Audio processing
    soundfile
    librosa
    pulsectl
    
    # Utilities
    pyyaml
    python-dotenv
    loguru
    
    # Kokoro TTS dependencies
    kokoroPackage
  ]);

  # Runtime wrapper for services
  serviceWrapper = name: modulePath: pkgs.writeShellApplication {
    name = "rt-${name}-service";
    runtimeInputs = [ pythonEnv pkgs.coreutils ];
    text = ''
      export PYTHONPATH="${pythonEnv}/lib/python3.12/site-packages:$PYTHONPATH"
      exec ${pythonEnv.interpreter} -m ${modulePath} "$@"
    '';
  };

  # Create service wrappers
  captureService = serviceWrapper "capture" "src.capture.capture_service";
  playbackService = serviceWrapper "playback" "src.playback.playback_service";
  translateService = serviceWrapper "translate" "src.translate.translate_service";
  ttsService = serviceWrapper "tts" "src.tts.tts_service";
  whisperService = serviceWrapper "whisper" "src.whisper.whisper_service";
  hybridWhisperService = serviceWrapper "whisper-hybrid" "src.whisper.hybrid_whisper_service";

in
{
  options.rt-translator = {
    enable = pkgs.lib.mkEnableOption "Real-time speech translation system";
  };

  config = pkgs.lib.mkIf config.rt-translator.enable {
    # Include the package in home.packages for easy access
    home.packages = [
      pythonEnv
      # Runtime wrappers
      captureService
      playbackService
      translateService
      ttsService
      whisperService
      hybridWhisperService
      # Pipewire utilities
      pkgs.pipewire
      pkgs.pulseaudio  # for pactl
    ];

    # Configure systemd user services
    systemd.user.services = {
      "rt-capture" = {
        description = "RT-Capture-service";
        requires = [ "rt-capture.socket" ];
        after = [ "rt-capture.socket" ];
        path = [ pythonEnv ];
        serviceConfig = {
          Type = "simple";
          ExecStart = "${captureService}/bin/rt-capture-service --socket-path %t/rt-capture.sock";
          Restart = "always";
          RestartSec = 5;
          Nice = -5;
          CPUSchedulingPolicy = "rr";
          IOSchedulingClass = "best-effort";
          Environment = [
            "PYTHONPATH=${pythonEnv}/${pythonEnv.sitePackages}"
          ];
        };
        
        install = {
          WantedBy = [ "default.target" ];
          Also = [ "rt-capture.socket" ];
        };
      };
      
      "rt-playback" = {
        description = "RT-Playback-service";
        requires = [ "rt-playback.socket" ];
        after = [ "rt-playback.socket" ];
        path = [ pythonEnv ];
        serviceConfig = {
          Type = "simple";
          ExecStart = "${playbackService}/bin/rt-playback-service --socket-path %t/rt-playback.sock";
          Restart = "always";
          RestartSec = 5;
          Nice = -5;
          CPUSchedulingPolicy = "rr";
          IOSchedulingClass = "best-effort";
          Environment = [
            "PYTHONPATH=${pythonEnv}/${pythonEnv.sitePackages}"
          ];
        };
        install = {
          WantedBy = [ "default.target" ];
          Also = [ "rt-playback.socket" ];
        };
      };
      
      "rt-translate" = {
        description = "RT-Translate-service";
        requires = [ "rt-translate.socket" ];
        after = [ "rt-translate.socket" ];
        path = [ pythonEnv ];
        serviceConfig = {
          Type = "simple";
          ExecStart = "${translateService}/bin/rt-translate-service --socket-path %t/rt-translate.sock";
          Restart = "always";
          RestartSec = 5;
          Nice = -5;
          CPUSchedulingPolicy = "rr";
          IOSchedulingClass = "best-effort";
          Environment = [
            "PYTHONPATH=${pythonEnv}/${pythonEnv.sitePackages}"
          ];
        };
        install = {
          WantedBy = [ "default.target" ];
          Also = [ "rt-translate.socket" ];
        };
      };
      
      "rt-tts" = {
        description = "RT-TTS-service";
        requires = [ "rt-tts.socket" ];
        after = [ "rt-tts.socket" ];
        path = [ pythonEnv ];
        serviceConfig = {
          Type = "simple";
          ExecStart = "${ttsService}/bin/rt-tts-service --socket-path %t/rt-tts.sock";
          Restart = "always";
          RestartSec = 5;
          Nice = -5;
          CPUSchedulingPolicy = "rr";
          IOSchedulingClass = "best-effort";
          Environment = [
            "PYTHONPATH=${pythonEnv}/${pythonEnv.sitePackages}"
          ];
        };
        install = {
          WantedBy = [ "default.target" ];
          Also = [ "rt-tts.socket" ];
        };
      };
      
      "rt-whisper" = {
        description = "RT-Whisper-service";
        requires = [ "rt-whisper.socket" ];
        after = [ "rt-whisper.socket" ];
        path = [ pythonEnv ];
        serviceConfig = {
          Type = "simple";
          ExecStart = "${whisperService}/bin/rt-whisper-service --socket-path %t/rt-whisper.sock";
          Restart = "always";
          RestartSec = 5;
          Nice = -5;
          CPUSchedulingPolicy = "rr";
          IOSchedulingClass = "best-effort";
          Environment = [
            "PYTHONPATH=${pythonEnv}/${pythonEnv.sitePackages}"
          ];
        };
        install = {
          WantedBy = [ "default.target" ];
          Also = [ "rt-whisper.socket" ];
        };
      };
      
      "rt-app" = {
        description = "Real-time Translator Application";
        after = [ "graphical-session.target" "pipewire.service" ];
        wants = [ "graphical-session.target" ];
        path = [ pythonEnv pkgs.pipewire pkgs.pulseaudio ];
        serviceConfig = {
          Type = "simple";
          ExecStart = "${pythonEnv.interpreter} -m src.main";
          Restart = "on-failure";
          RestartSec = 5;
          Environment = [
            "PYTHONPATH=${pythonEnv}/${pythonEnv.sitePackages}"
          ];
        };
        install = {
          WantedBy = [ "default.target" ];
        };
      };
    };
    
    # Ensure pipewire is properly configured in the user environment
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

    # Configure systemd user sockets
    systemd.user.sockets = {
      "rt-capture" = {
        description = "RT-Capture-socket";
        wantedBy = [ "sockets.target" ];
        socketConfig = {
          ListenStream = "%t/rt-capture.sock";
          SocketMode = "0660";
        };
      };
      
      "rt-playback" = {
        description = "RT-Playback-socket";
        wantedBy = [ "sockets.target" ];
        socketConfig = {
          ListenStream = "%t/rt-playback.sock";
          SocketMode = "0660";
        };
      };
      
      "rt-translate" = {
        description = "RT-Translation-socket";
        wantedBy = [ "sockets.target" ];
        socketConfig = {
          ListenStream = "%t/rt-translate.sock";
          SocketMode = "0660";
        };
      };
      
      "rt-tts" = {
        description = "RT-TTS-socket";
        wantedBy = [ "sockets.target" ];
        socketConfig = {
          ListenStream = "%t/rt-tts.sock";
          SocketMode = "0660";
        };
      };
      
      "rt-whisper" = {
        description = "RT-Whisper-socket";
        wantedBy = [ "sockets.target" ];
        socketConfig = {
          ListenStream = "%t/rt-whisper.sock";
          SocketMode = "0660";
        };
      };
    };
  };
}