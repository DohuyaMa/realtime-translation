{ python3
, lib
, callPackage
}:

callPackage ./common.nix {
  pname = "translator-hybrid-whisper";
  src = ../../..;

  dependencies = with python3.pkgs; [
    # Hybrid Whisper service specific dependencies
    faster-whisper
    kokoro
    numpy
    onnxruntime
    soundfile
    librosa
    pulsectl
    pyyaml
    python-dotenv
    loguru
    # HuggingFace Hub for model downloading
    huggingface-hub
    # Wyoming protocol for connecting to wyoming-faster-whisper
    wyoming
  ];
}