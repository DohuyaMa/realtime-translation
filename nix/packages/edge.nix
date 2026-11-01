{ lib, python313Packages, pulseaudio }:
let
  sources = import ./sources.nix { inherit lib; };
in
python313Packages.buildPythonApplication {
  pname = "rt-edge";
  version = "0.1.0";
  pyproject = true;
  src = sources.edgeSrc;
  build-system = [ python313Packages.setuptools ];
  dependencies = import ./edge-deps.nix python313Packages;
  # capture (parec), playback (pacat) and the PipeWire preflight (pactl)
  makeWrapperArgs = [ "--prefix" "PATH" ":" (lib.makeBinPath [ pulseaudio ]) ];
  # pyproject also declares rt-asr/rt-phonetic/rt-tts entry points; their modules are not in edgeSrc.
  postInstall = ''
    find $out/bin -mindepth 1 -maxdepth 1 ! -name translator-ui -delete
  '';
  pythonImportsCheck = [
    "src.translation_system"
    "src.clients"
    "src.adapters.direct_adapter"
  ];
  meta = {
    description = "Real-time translator edge app (UI, capture, playback; remote ASR/LLM/TTS)";
    mainProgram = "translator-ui";
    platforms = [ "x86_64-linux" ];
  };
}
