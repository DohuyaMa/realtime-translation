{ python3
, lib
, callPackage
}:

callPackage ./common.nix {
  pname = "translator-hybrid-whisper";
  src = ../../../src;

  dependencies = with python3.pkgs; [
    # Hybrid Whisper service specific dependencies
    faster-whisper
    kokoro-onnx
    numpy
    onnxruntime
    soundfile
    librosa
    pulsectl
    pyyaml
    python-dotenv
    loguru
  ];
}