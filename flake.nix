{
  description = "Real-time speech translation system";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};

        # Python packages
        pythonPackages = pkgs.python311Packages;
        pythonEnv = pythonPackages.buildPythonApplication {
          pname = "real-time-translator";
          version = "0.1.0";
          src = ./.;
          
          propagatedBuildInputs = with pythonPackages; [
            # Core dependencies
            pyqt5
            pyaudio
            numpy
            sounddevice
            
            # AI and ML
            torch
            transformers
            openai-whisper
            onnxruntime
            
            # Audio processing
            soundfile
            librosa
            pipewire-python
            pulsectl
            
            # Utilities
            pyyaml
            python-dotenv
            loguru
            
            # Kokoro TTS dependencies (from requirements)
            (pkgs.python311.pkgs.buildPythonPackage rec {
              pname = "kokoro-onnx";
              version = "0.4.9";
              
              src = pkgs.fetchFromGitHub {
                owner = "thewh1teagle";
                repo = "kokoro-onnx";
                rev = "v${version}";
                sha256 = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=";
              };
              
              propagatedBuildInputs = with pythonPackages; [
                onnxruntime
                colorlog
                espeakng-loader
                phonemizer-fork
                numpy
              ];
              
              pythonImportsCheck = [ "kokoro_onnx" ];
            })
          ];
          
          doCheck = false;
        };

        # System dependencies
        systemPackages = with pkgs; [
          # Core system tools
          git
          docker
          docker-compose
          just
          
          # Audio tools
          pipewire
          pulseaudio
          alsa-utils
          pavucontrol
          
          # AI/ML tools
          ollama
          
          # Development tools
          nodejs
          pnpm
          python311
          python311Packages.pip
          python311Packages.virtualenv
          
          # Libraries needed for audio processing
          libffi
          openssl
          zlib
          
          # Additional system libraries
          gcc
          gnumake
          pkg-config
        ];

      in
      {
        devShells.default = pkgs.mkShell {
          buildInputs = systemPackages ++ [ pythonEnv ];
          
          # Environment variables
          PYTHONPATH = "${pythonEnv}/${pythonPackages.python.sitePackages}";
          PIP_DISABLE_PIP_VERSION_CHECK = "1";
          
          # Setup hooks
          shellHook = ''
            export PYTHONPATH="$PWD:$PYTHONPATH"
            echo "Real-time Translator development environment ready!"
            echo "Use 'just run' or 'python3 -m src.main' to start the application"
          '';
        };
        
        packages.default = pythonEnv;
        
        apps.default = {
          type = "app";
          program = "${pythonEnv}/bin/real-time-translator";
        };
      });
}