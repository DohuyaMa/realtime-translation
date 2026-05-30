{ python3
, lib
, callPackage
}:

callPackage ./common.nix {
  pname = "translator-hybrid-whisper";
  src = ../../..;

  dependencies = with python3.pkgs; [
    faster-whisper
    numpy
    pyyaml
    python-dotenv
    loguru
    huggingface-hub
    wyoming
  ];
}