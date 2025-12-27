{ python3
, lib
, callPackage
}:

callPackage ./common.nix {
  pname = "translator-capture";
  src = ../../../src;

  dependencies = with python3.pkgs; [
    # Capture service specific dependencies
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
