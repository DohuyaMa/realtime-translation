{ pkgs, ... }:

let
  # Python packages
  pythonPackages = pkgs.python313Packages;
  kokoroPackage = pythonPackages.kokoro;

  # en_core_web_sm — spaCy model for misaki G2P (kokoro TTS dependency).
  # spaZy tries to pip install this at runtime (and fails in Nix).
  en_core_web_sm = pythonPackages.buildPythonPackage {
    pname = "en_core_web_sm";
    version = "3.8.0";
    format = "wheel";
    src = pkgs.fetchurl {
      url = "https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl";
      sha256 = "11gvl30zfa49rwkfnbm5yja7gwaxq37k8szdvvrvzm17nyfl4chr";
    };
    doCheck = false;
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
    python313
    python313Packages.pip
    python313Packages.virtualenv
    
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
  devShells = {
    default = pkgs.mkShell {
      buildInputs = systemPackages ++ [
        # Create a combined Python environment with all required packages
        (pkgs.python313.withPackages (ps: with ps; [
          pyaudio
          numpy
          sounddevice
          torch
          transformers
          faster-whisper
          ctranslate2
          onnxruntime
          pyside6
          soundfile
          librosa
          pulsectl
          pyyaml
          python-dotenv
          loguru
          kokoroPackage
          sentencepiece
          en_core_web_sm
          pytest
        ]))
      ] ++ [
        # Wyoming faster-whisper for development
        pkgs.wyoming-faster-whisper
      ];
      
      # Environment variables
      PIP_DISABLE_PIP_VERSION_CHECK = "1";
      HF_HOME = "$HOME/.cache/huggingface";
      TRANSFORMERS_CACHE = "$HOME/.cache/transformers";
      HF_HUB_CACHE = "$HOME/.cache/huggingface/hub";
      
      # Setup hooks
      shellHook = ''
         export HF_HOME="$HOME/.cache/huggingface"
         export TRANSFORMERS_CACHE="$HOME/.cache/transformers"
         export HF_HUB_CACHE="$HOME/.cache/huggingface/hub"
         
         # Create cache directories
         mkdir -p "$HOME/.cache/huggingface"
         mkdir -p "$HOME/.cache/transformers"
         mkdir -p "$HOME/.cache/huggingface/hub"
         
         # Check if PipeWire virtual sinks already exist, create them if not
         # (devShell is isolated and doesn't use systemd user services)
         if ! pactl list sinks short | grep -q rt_virtual_input; then
           pactl load-module module-null-sink sink_name=rt_virtual_input sink_properties=device.description="RT-Virtual-Input" || true
         fi
         if ! pactl list sinks short | grep -q rt_virtual_output; then
           pactl load-module module-null-sink sink_name=rt_virtual_output sink_properties=device.description="RT-Virtual-Output" || true
         fi
         
         echo "Real-time Translator development environment ready!"
         echo "Use 'python3 -m src.main' to start the application"
         echo ""
         echo "PipeWire virtual sinks have been created:"
         echo "  - rt_virtual_input (RT-Virtual-Input)"
         echo "  - rt_virtual_output (RT-Virtual-Output)"
         echo "These are available for audio routing in the development environment."
         echo ""
         echo "Wyoming faster-whisper ready: run ./result/bin/rt-whisper-wyoming"
       '';
    };
  };
}