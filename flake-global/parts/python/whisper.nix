{ python3
, lib
, callPackage
}:

callPackage ./common.nix {
  pname = "translator-whisper";
  src = ../../..;

  dependencies = with python3.pkgs; [
    # Whisper service specific dependencies
    whisper
    torch
    transformers
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