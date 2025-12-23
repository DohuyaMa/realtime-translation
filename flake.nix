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
    let
      # Define the Home Manager module separately (not in eachSystem)
      rtTranslatorModule = { config, pkgs, ... }: 
        let
          # Python packages
          pythonPackages = pkgs.python312Packages;
          
          # Required packages
          kokoroPackage = pythonPackages.kokoro;
          
          # Create proper Python environment for runtime
          pythonEnv = pkgs.python312.withPackages (ps: with ps; [
            # Core dependencies
            pyaudio
            numpy
            sounddevice
            
            # AI and ML
            torch
            transformers
            openai-whisper
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
        };
in
{
  # Home Manager module
  homeManagerModules.rt-translator = rtTranslatorModule;

  # NixOS module for production deployment
  nixosModules.virtual-sinks = import ./nixosModules/virtual-sinks.nix;

  # System-specific outputs using flake-utils
} // flake-utils.lib.eachSystem ["x86_64-linux"] (system:
  let
    pkgs = nixpkgs.legacyPackages.${system};

    # Python packages
    pythonPackages = pkgs.python312Packages;
    
    # Use the official kokoro package from nixpkgs
    kokoroPackage = pythonPackages.kokoro;
    
    # Create application package using mkDerivation instead of buildPythonPackage
    appPackage = pkgs.stdenv.mkDerivation {
      pname = "real-time-translator";
      version = "0.1.0";
      src = ./.;
      
      nativeBuildInputs = with pkgs; [ makeWrapper python312 ];
      propagatedBuildInputs = with pythonPackages; [
        # Core dependencies
        pyaudio
        numpy
        sounddevice
        
        # AI and ML
        torch
        transformers
        openai-whisper
        onnxruntime
        pyside6
        
        # Audio processing
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
      ];
      
      buildPhase = "true"; # Skip build phase
      
      installPhase = ''
        runHook preInstall
        mkdir -p $out/bin $out/share/real-time-translator
        
        # Copy source files, excluding result symlink and .git
        cp -r $src/* $out/share/real-time-translator/
        rm -rf $out/share/real-time-translator/.git
        # Remove result symlink if it exists to avoid broken symlink errors
        rm -f $out/share/real-time-translator/result
        
        # Create wrapper script for the application
        makeWrapper ${pythonPackages.python.interpreter} $out/bin/real-time-translator \
          --prefix PYTHONPATH : "$out/share/real-time-translator" \
          --add-flags "-m src.main"
      '';
      
      doInstallCheck = false;
    };

    # System dependencies for devShell
    systemPackages = with pkgs; [
      # Core system tools
      just
      
      # Qt dependencies for GUI (moved from systemd services)
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
      
      # Audio tools (using pipewire instead of conflicting pulseaudio)
      pipewire
      pulseaudio      # for pactl compatibility
      alsa-utils
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
        openai-whisper
        onnxruntime
        pyside6
        soundfile
        librosa
        pulsectl
        pyyaml
        python-dotenv
        loguru
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
               
               # Restart PipeWire and create virtual sinks for devShell
               # (devShell is isolated and doesn't use systemd user services)
               systemctl --user restart pipewire pipewire-pulse || true
               pactl load-module module-null-sink sink_name=rt_virtual_input sink_properties=device.description="RT-Virtual-Input" || true
               pactl load-module module-null-sink sink_name=rt_virtual_output sink_properties=device.description="RT-Virtual-Output" || true
               
               echo "Real-time Translator development environment ready!"
               echo "Use 'python3 -m src.main' to start the application"
               echo ""
               echo "PipeWire virtual sinks have been created:"
               echo "  - rt_virtual_input (RT-Virtual-Input)"
               echo "  - rt_virtual_output (RT-Virtual-Output)"
               echo "These are available for audio routing in the development environment."
             '';
    };
    
    packages.default = appPackage;
    
    apps.default = {
      type = "app";
      program = "${appPackage}/bin/real-time-translator";
    };
  }
);
}
