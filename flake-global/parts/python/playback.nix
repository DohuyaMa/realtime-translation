{ python3
, lib
, callPackage
}:

callPackage ./common.nix {
  pname = "translator-playback";
  src = ../../..;

  dependencies = with python3.pkgs; [
    pyaudio
    sounddevice
    numpy
    pulsectl
    pyyaml
    python-dotenv
    loguru
  ];
}