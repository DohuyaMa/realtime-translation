{ python3
, lib
, callPackage
}:

callPackage ./common.nix {
  pname = "translator-whisper";
  src = ../../..;

  dependencies = with python3.pkgs; [
    faster-whisper
    numpy
    pyyaml
    python-dotenv
    loguru
  ];
}