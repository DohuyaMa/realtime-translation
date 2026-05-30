{ python3
, lib
, callPackage
, fetchurl
}:

let
  en_core_web_sm = python3.pkgs.buildPythonPackage {
    pname = "en_core_web_sm";
    version = "3.8.0";
    format = "wheel";
    src = fetchurl {
      url = "https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl";
      sha256 = "11gvl30zfa49rwkfnbm5yja7gwaxq37k8szdvvrvzm17nyfl4chr";
    };
    doCheck = false;
  };
in

callPackage ./common.nix {
  pname = "translator-tts";
  src = ../../..;

  dependencies = with python3.pkgs; [
    kokoro
    en_core_web_sm
    numpy
    sounddevice
    pulsectl
    pyyaml
    python-dotenv
    loguru
  ];
}