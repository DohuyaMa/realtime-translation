{ python3
, lib
, callPackage
}:

callPackage ./common.nix {
  pname = "translator-playback";
  src = ../../../src;

  dependencies = with python3.pkgs; [
    # Playback service specific dependencies
    pyaudio
    sounddevice
    numpy
    soundfile
    librosa
    pulsectl
    pyyaml
    python-dotenv
    loguru
  ];
}