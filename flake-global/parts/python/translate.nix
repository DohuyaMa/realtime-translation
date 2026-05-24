{ python3
, lib
, callPackage
}:

callPackage ./common.nix {
  pname = "translator-translate";
  src = ../../..;

  dependencies = with python3.pkgs; [
    # Translation service specific dependencies
    transformers
    torch
    sentencepiece
    sacremoses
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