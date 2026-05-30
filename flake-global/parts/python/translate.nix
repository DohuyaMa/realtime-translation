{ python3
, lib
, callPackage
}:

callPackage ./common.nix {
  pname = "translator-translate";
  src = ../../..;

  dependencies = with python3.pkgs; [
    transformers
    torch-bin
    sentencepiece
    sacremoses
    numpy
    pyyaml
    python-dotenv
    loguru
  ];
}