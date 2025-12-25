{ pkgs, ... }:

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

  # Create application package using mkDerivation instead of buildPythonPackage
  appPackage = pkgs.stdenv.mkDerivation {
    pname = "real-time-translator";
    version = "0.1.0";
    src = ../../../.;  # Go up 3 levels to reach project root
    
    nativeBuildInputs = with pkgs; [ makeWrapper python313 ];
    propagatedBuildInputs = with pythonPackages; [
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

in
{
  packages = {
    default = appPackage;
    inherit appPackage captureService playbackService translateService ttsService whisperService hybridWhisperService;
  };
}