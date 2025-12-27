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
  # Define virtual sinks service
  virtualSinksService = pkgs.writeShellApplication {
    name = "rt-virtual-sinks-service";
    runtimeInputs = [ pkgs.pipewire pkgs.pulseaudio ];
    text = ''
      pactl load-module module-null-sink sink_name=rt_virtual_input sink_properties=device.description="RT Virtual Input"
      pactl load-module module-null-sink sink_name=rt_virtual_output sink_properties=device.description="RT Virtual Output (Microphone)"
    '';
  };
};

in
{
  options.rt-translator = {
    enable = pkgs.lib.mkEnableOption "Real-time speech translation system";
  };

  config = pkgs.lib.mkIf config.rt-translator.enable {
    # Include the package in home.packages for easy access
    home.packages = [
      pythonEnv
      # Import the services from the flake packages
      pkgs.rt-capture-service
      pkgs.rt-playback-service
      pkgs.rt-translate-service
      pkgs.rt-tts-service
      pkgs.rt-whisper-service
      pkgs.rt-whisper-hybrid-service
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
        path = [ pkgs.rt-capture-service ];
        serviceConfig = {
          Type = "simple";
          ExecStart = "${pkgs.rt-capture-service}/bin/rt-capture-service --socket-path %t/rt-capture.sock";
          Restart = "always";
          RestartSec = 5;
          Nice = -5;
          CPUSchedulingPolicy = "rr";
          IOSchedulingClass = "best-effort";
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
        path = [ pkgs.rt-playback-service ];
        serviceConfig = {
          Type = "simple";
          ExecStart = "${pkgs.rt-playback-service}/bin/rt-playback-service --socket-path %t/rt-playback.sock";
          Restart = "always";
          RestartSec = 5;
          Nice = -5;
          CPUSchedulingPolicy = "rr";
          IOSchedulingClass = "best-effort";
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
        path = [ pkgs.rt-translate-service ];
        serviceConfig = {
          Type = "simple";
          ExecStart = "${pkgs.rt-translate-service}/bin/rt-translate-service --socket-path %t/rt-translate.sock";
          Restart = "always";
          RestartSec = 5;
          Nice = -5;
          CPUSchedulingPolicy = "rr";
          IOSchedulingClass = "best-effort";
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
        path = [ pkgs.rt-tts-service ];
        serviceConfig = {
          Type = "simple";
          ExecStart = "${pkgs.rt-tts-service}/bin/rt-tts-service --socket-path %t/rt-tts.sock";
          Restart = "always";
          RestartSec = 5;
          Nice = -5;
          CPUSchedulingPolicy = "rr";
          IOSchedulingClass = "best-effort";
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
        path = [ pkgs.rt-whisper-service ];
        serviceConfig = {
          Type = "simple";
          ExecStart = "${pkgs.rt-whisper-service}/bin/rt-whisper-service --socket-path %t/rt-whisper.sock";
          Restart = "always";
          RestartSec = 5;
          Nice = -5;
          CPUSchedulingPolicy = "rr";
          IOSchedulingClass = "best-effort";
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
        requires = [ "rt-virtual-sinks.service" ];
        path = [ pkgs.real-time-translator pkgs.pipewire pkgs.pulseaudio ];
        serviceConfig = {
          Type = "exec";
          ExecStart = "${pkgs.real-time-translator}/bin/translator-ui";
          Restart = "on-failure";
          RestartSec = 5;
          Environment = [
            "PATH=%h/.nix-profile/bin:/run/wrappers/bin:/etc/profiles/per-user/%h/bin:/nix/var/nix/profiles/default/bin:/run/current-system/sw/bin"
          ];
        };
        install = {
          WantedBy = [ "default.target" ];
        };
      };
      
      "rt-hybrid-whisper" = {
        description = "RT-Hybrid-Whisper-service (Wyoming Integration)";
        requires = [ "rt-hybrid-whisper.socket" ];
        after = [ "rt-hybrid-whisper.socket" "network.target" ];
        path = [ pkgs.rt-whisper-hybrid-service ];
        serviceConfig = {
          Type = "simple";
          ExecStart = "${pkgs.rt-whisper-hybrid-service}/bin/rt-whisper-hybrid-service --socket-path %t/rt-hybrid-whisper.sock --use-wyoming --wyoming-host localhost --wyoming-port 10300";
          Restart = "always";
          RestartSec = 5;
          Nice = -5;
          CPUSchedulingPolicy = "rr";
          IOSchedulingClass = "best-effort";
          Environment = [
            "PYTHONUNBUFFERED=1"
          ];
        };
        install = {
          WantedBy = [ "default.target" ];
          Also = [ "rt-hybrid-whisper.socket" ];
        };
      };
      
      "rt-virtual-sinks" = {
        description = "Create RT Virtual Sinks";
        after = [ "pipewire.service" "pipewire-pulse.service" ];
        wants = [ "pipewire.service" "pipewire-pulse.service" ];
        path = [ pkgs.pipewire pkgs.pulseaudio ];
        serviceConfig = {
          Type = "oneshot";
          ExecStart = "${virtualSinksService}/bin/rt-virtual-sinks-service";
          RemainAfterExit = true;
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
      
      "rt-hybrid-whisper" = {
        description = "RT-Hybrid-Whisper-socket";
        wantedBy = [ "sockets.target" ];
        socketConfig = {
          ListenStream = "%t/rt-hybrid-whisper.sock";
          SocketMode = "0660";
        };
      };
    };
  };
};