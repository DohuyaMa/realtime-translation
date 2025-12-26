{ pkgs, lib, projectRoot ? ../.. }:

let
  # Import individual service packages from parts/python
  python3 = pkgs.python313;
  buildPythonApplication = python3.pkgs.buildPythonApplication;
  fetchPypi = pkgs.fetchPypi;
  
  capturePackage = import ../../parts/python/capture.nix {
    inherit lib python3 buildPythonApplication fetchPypi pkgs;
  };
  playbackPackage = import ../../parts/python/playback.nix {
    inherit lib python3 buildPythonApplication fetchPypi pkgs;
  };
  translatePackage = import ../../parts/python/translate.nix {
    inherit lib python3 buildPythonApplication fetchPypi pkgs;
  };
  ttsPackage = import ../../parts/python/tts.nix {
    inherit lib python3 buildPythonApplication fetchPypi pkgs;
  };
  whisperPackage = import ../../parts/python/whisper.nix {
    inherit lib python3 buildPythonApplication fetchPypi pkgs;
  };
  hybridWhisperPackage = import ../../parts/python/hybrid-whisper.nix {
    inherit lib python3 buildPythonApplication fetchPypi pkgs;
  };
  uiPackage = import ../../parts/python/ui.nix {
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