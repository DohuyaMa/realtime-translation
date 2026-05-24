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
    torch-bin
    transformers
    sounddevice
    soundfile
    pyaudio
    faster-whisper
    ctranslate2
    kokoro
    en_core_web_sm
    sentencepiece
    sacremoses
    # HuggingFace Hub for model downloading
    huggingface-hub
    # Wyoming protocol for connecting to wyoming-faster-whisper
    wyoming
  ];
}