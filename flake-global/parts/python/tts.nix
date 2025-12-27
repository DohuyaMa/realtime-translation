{ python3
, lib
, callPackage
}:

callPackage ./common.nix {
  pname = "translator-tts";
  src = ../../../src;

  dependencies = with python3.pkgs; [
    # TTS service specific dependencies
    pyttsx3
    gtts
    playsound
    kokoro-onnx
    numpy
    soundfile
    librosa
    pulsectl
    pyyaml
    python-dotenv
    loguru
  ];
}