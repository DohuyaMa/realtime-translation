{ pkgs, lib, projectSource ? ./. }:

let
  # Import individual service packages from parts/python
  python3 = pkgs.python313;
  buildPythonApplication = python3.pkgs.buildPythonApplication;
  fetchPypi = pkgs.fetchPypi;
  
  capturePackage = import (projectSource + "/parts/python/capture.nix") {
    inherit lib python3 buildPythonApplication fetchPypi pkgs;
  };
  playbackPackage = import (projectSource + "/parts/python/playback.nix") {
    inherit lib python3 buildPythonApplication fetchPypi pkgs;
  };
  translatePackage = import (projectSource + "/parts/python/translate.nix") {
    inherit lib python3 buildPythonApplication fetchPypi pkgs;
  };
  ttsPackage = import (projectSource + "/parts/python/tts.nix") {
    inherit lib python3 buildPythonApplication fetchPypi pkgs;
  };
  whisperPackage = import (projectSource + "/parts/python/whisper.nix") {
    inherit lib python3 buildPythonApplication fetchPypi pkgs;
  };
  hybridWhisperPackage = import (projectSource + "/parts/python/hybrid-whisper.nix") {
    inherit lib python3 buildPythonApplication fetchPypi pkgs;
  };
  uiPackage = import (projectSource + "/parts/python/ui.nix") {
    inherit lib python3 buildPythonApplication fetchPypi pkgs;
  };

in
{
  packages = {
    default = uiPackage; # Default to UI package
    inherit capturePackage playbackPackage translatePackage ttsPackage whisperPackage hybridWhisperPackage uiPackage;
    captureService = capturePackage;
    playbackService = playbackPackage;
    translateService = translatePackage;
    ttsService = ttsPackage;
    whisperService = whisperPackage;
    hybridWhisperService = hybridWhisperPackage;
    appPackage = uiPackage;
  };
}