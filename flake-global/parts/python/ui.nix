{ python3
, lib
, callPackage
}:

callPackage ./common.nix {
  pname = "translator-ui";
  src = ../../..;

  dependencies = with python3.pkgs; [
    pyside6
    pulsectl
    pyyaml
    python-dotenv
    loguru
    # Runtime imports from translation_system and adapters
    numpy
    torch
    transformers
    sounddevice
    soundfile
    pyaudio
    faster-whisper
    ctranslate2
    kokoro
    sentencepiece
    sacremoses
    # HuggingFace Hub for model downloading
    huggingface-hub
    # Wyoming protocol for connecting to wyoming-faster-whisper
    wyoming
  ];
}