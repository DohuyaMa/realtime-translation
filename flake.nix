{
  description = "Real-time speech translation system";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";
    flake-utils.url = "github:numtide/flake-utils";
    home-manager = {
      url = "github:nix-community/home-manager";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, flake-utils, home-manager }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};

        # Python packages
        pythonPackages = pkgs.python312Packages;
        
        # Required packages
        kokoroPackage = pythonPackages.kokoro;
        whisperPackage = pythonPackages.openai-whisper;
        alsaUtils = pkgs.alsa-utils;

        pythonEnv = pythonPackages.buildPythonPackage {
          pname = "real-time-translator";
          version = "0.1.0";
          format = "other";
          src = ./.;
          
          propagatedBuildInputs = with pythonPackages; [
            # Core dependencies
            pyaudio
            numpy
            sounddevice
            
            # AI and ML
            torch
            transformers
            whisperPackage
            onnxruntime
            
            # Audio processing
            soundfile
            librosa
            pulsectl
            
            # Utilities
            pyyaml
            python-dotenv
            loguru
            
            # UI
            pyqt6
            
            # Kokoro TTS dependencies
            kokoroPackage
          ];
          
          # Completely disable all phases that might trigger python-imports-check-hook
          # Skip the build phase entirely - this is just a script wrapper
          buildPhase = "true";
          
          # Simple install phase
          installPhase = ''
            runHook preInstall
            mkdir -p $out/bin $out/share/real-time-translator
            
            # Copy source files
            cp -r $src/* $out/share/real-time-translator/
            rm -rf $out/share/real-time-translator/.git
            
            # Create wrapper script for the application
            makeWrapper ${pythonPackages.python.interpreter} $out/bin/real-time-translator \
              --prefix PYTHONPATH : "$out/share/real-time-translator" \
              --add-flags "-m src.main"

          '';
          
          # Disable phases that cause issues
          checkPhase = "true";
          
          # Make sure we have the right dependencies for makeWrapper
          nativeBuildInputs = with pkgs; [ makeWrapper ];
        };

        # System dependencies
        # System dependencies
        systemPackages = with pkgs; [
          # Core system tools
          just
          
          # Qt dependencies for GUI
          qt6.qtbase
          qt6.qtwayland
          xorg.libX11
          xorg.libXext
          xorg.libXrender
          xorg.libXrandr
          xorg.libXfixes
          libGL
          
          # Development tools
          nodejs
          pnpm
          python312
          python312Packages.pip
          python312Packages.virtualenv
          
          # Libraries needed for audio processing
          libffi
          openssl
          zlib
          
          # Additional system libraries
          gcc
          gnumake
          pkg-config
          ninja
        ];
      in
      {
        devShells.default = pkgs.mkShell {
          buildInputs = systemPackages ++ (with pythonPackages; [
            # Individual packages instead of the combined pythonEnv that needs building
            pyaudio
            numpy
            sounddevice
            torch
            transformers
            whisperPackage
            onnxruntime
            soundfile
            librosa
            pulsectl
            pyyaml
            python-dotenv
            loguru
            pyqt6
            kokoroPackage
            pytest
          ]);
          
          # Environment variables
          PIP_DISABLE_PIP_VERSION_CHECK = "1";
          HF_HOME = "$HOME/.cache/huggingface";
          TRANSFORMERS_CACHE = "$HOME/.cache/transformers";
          HF_HUB_CACHE = "$HOME/.cache/huggingface/hub";
          
          # Setup hooks
          shellHook = ''
            export PYTHONPATH="$PWD:$PYTHONPATH"
            export HF_HOME="$HOME/.cache/huggingface"
            export TRANSFORMERS_CACHE="$HOME/.cache/transformers"
            export HF_HUB_CACHE="$HOME/.cache/huggingface/hub"
            
            # Create cache directories
            mkdir -p "$HOME/.cache/huggingface"
            mkdir -p "$HOME/.cache/transformers"
            mkdir -p "$HOME/.cache/huggingface/hub"
            
            echo "Real-time Translator development environment ready!"
            echo "Use 'python3 -m src.main' to start the application"
            echo ""
            echo "Note: Make sure your PipeWire virtual sinks are set up."
            echo "Run this once to set up virtual sinks if not already done:"
            echo "  python install_pipewire_config.py"
            echo "  # Or manually: systemctl --user restart pipewire pipewire-pulse"
          '';
        };
        
        packages.default = pythonEnv;
        
        apps.default = {
          type = "app";
          program = "${pythonEnv}/bin/real-time-translator";
        };

        # User-level systemd units via home-manager
        homeManagerConfiguration = {
          # Import home-manager modules
          imports = [ home-manager.homeManagerModules.home-manager ];
          
          # Home manager configuration
          home.stateVersion = "24.11"; # Set this to your NixOS version
          
          # Include the package in home.packages for easy access
          home.packages = [ pythonEnv ];
          
          # Configure systemd user services
          systemd.user.services = {
            "rt-capture" = {
              description = "RT Capture service";
              requires = [ "rt-capture.socket" ];
              after = [ "rt-capture.socket" ];
              serviceConfig = {
                Type = "simple";
                ExecStart = "${pkgs.python312}/bin/python -m src.capture.capture_service --socket-path %t/rt-capture.sock";
                Restart = "always";
                RestartSec = 5;
              };
              install = {
                WantedBy = [ "default.target" ];
                Also = [ "rt-capture.socket" ];
              };
            };
            
            "rt-playback" = {
              description = "RT Playback service";
              requires = [ "rt-playback.socket" ];
              after = [ "rt-playback.socket" ];
              serviceConfig = {
                Type = "simple";
                ExecStart = "${pkgs.python312}/bin/python -m src.playback_service --socket-path %t/rt-playback.sock";
                Restart = "always";
                RestartSec = 5;
              };
              install = {
                WantedBy = [ "default.target" ];
                Also = [ "rt-playback.socket" ];
              };
            };
            
            "rt-translate" = {
              description = "RT Translation service";
              requires = [ "rt-translate.socket" ];
              after = [ "rt-translate.socket" ];
              serviceConfig = {
                Type = "simple";
                ExecStart = "${pkgs.python312}/bin/python -m src.translate.translate_service --socket-path %t/rt-translate.sock";
                Restart = "always";
                RestartSec = 5;
              };
              install = {
                WantedBy = [ "default.target" ];
                Also = [ "rt-translate.socket" ];
              };
            };
            
            "rt-tts" = {
              description = "RT TTS service";
              requires = [ "rt-tts.socket" ];
              after = [ "rt-tts.socket" ];
              serviceConfig = {
                Type = "simple";
                ExecStart = "${pkgs.python312}/bin/python -m src.tts.tts_service --socket-path %t/rt-tts.sock";
                Restart = "always";
                RestartSec = 5;
              };
              install = {
                WantedBy = [ "default.target" ];
                Also = [ "rt-tts.socket" ];
              };
            };
            
            "rt-whisper" = {
              description = "RT Whisper service";
              requires = [ "rt-whisper.socket" ];
              after = [ "rt-whisper.socket" ];
              serviceConfig = {
                Type = "simple";
                ExecStart = "${pkgs.python312}/bin/python -m src.whisper.whisper_service --socket-path %t/rt-whisper.sock";
                Restart = "always";
                RestartSec = 5;
              };
              install = {
                WantedBy = [ "default.target" ];
                Also = [ "rt-whisper.socket" ];
              };
            };
            
            "rt-virtual-sinks" = {
              description = "Create RT Virtual Sinks";
              after = [ "pipewire.service" "pipewire-pulse.service" ];
              wants = [ "pipewire.service" "pipewire-pulse.service" ];
              serviceConfig = {
                Type = "oneshot";
                ExecStart = "${pkgs.bash}/bin/bash -c \"${pkgs.pulseaudio}/bin/pactl load-module module-null-sink sink_name=rt_virtual_input sink_properties=device.description='RT Virtual Input' && ${pkgs.pulseaudio}/bin/pactl load-module module-null-sink sink_name=rt_virtual_output sink_properties=device.description='RT Virtual Output (Microphone)'\"";
                RemainAfterExit = "yes";
              };
              install = {
                WantedBy = [ "default.target" ];
              };
            };
          };

          # Configure systemd user sockets
          systemd.user.sockets = {
            "rt-capture" = {
              description = "RT Capture socket";
              wantedBy = [ "sockets.target" ];
              socketConfig = {
                ListenStream = "%t/rt-capture.sock";
                SocketMode = "0660";
              };
            };
            
            "rt-playback" = {
              description = "RT Playback socket";
              wantedBy = [ "sockets.target" ];
              socketConfig = {
                ListenStream = "%t/rt-playback.sock";
                SocketMode = "0660";
              };
            };
            
            "rt-translate" = {
              description = "RT Translation socket";
              wantedBy = [ "sockets.target" ];
              socketConfig = {
                ListenStream = "%t/rt-translate.sock";
                SocketMode = "0660";
              };
            };
            
            "rt-tts" = {
              description = "RT TTS socket";
              wantedBy = [ "sockets.target" ];
              socketConfig = {
                ListenStream = "%t/rt-tts.sock";
                SocketMode = "0660";
              };
            };
            
            "rt-whisper" = {
              description = "RT Whisper socket";
              wantedBy = [ "sockets.target" ];
              socketConfig = {
                ListenStream = "%t/rt-whisper.sock";
                SocketMode = "0660";
              };
            };
          };
        };
      });
}