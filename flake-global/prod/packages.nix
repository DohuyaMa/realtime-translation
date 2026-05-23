{ pkgs, lib }:

let
  # Import individual service packages from parts/python
  python3 = pkgs.python313;
  buildPythonApplication = python3.pkgs.buildPythonApplication;
  fetchPypi = pkgs.fetchPypi;
  
  capturePackage = pkgs.callPackage ../parts/python/capture.nix {
    inherit python3 lib;
  };
  playbackPackage = pkgs.callPackage ../parts/python/playback.nix {
    inherit python3 lib;
  };
  translatePackage = pkgs.callPackage ../parts/python/translate.nix {
    inherit python3 lib;
  };
  ttsPackage = pkgs.callPackage ../parts/python/tts.nix {
    inherit python3 lib;
  };
  whisperPackage = pkgs.callPackage ../parts/python/whisper.nix {
    inherit python3 lib;
  };
  hybridWhisperPackage = pkgs.callPackage ../parts/python/hybrid-whisper.nix {
    inherit python3 lib;
  };
  uiPackage = pkgs.callPackage ../parts/python/ui.nix {
    inherit python3 lib;
  };

in

  {
    packages = {
      default = uiPackage; # Default to UI package
      inherit capturePackage playbackPackage translatePackage ttsPackage whisperPackage hybridWhisperPackage uiPackage;
      # Simple names for tests
      capture = capturePackage;
      playback = playbackPackage;
      translate = translatePackage;
      tts = ttsPackage;
      whisper = whisperPackage;
      "hybrid-whisper" = hybridWhisperPackage;
      ui = uiPackage;
      app = uiPackage;
      # Service names for home-manager module
      captureService = capturePackage;
      playbackService = playbackPackage;
      translateService = translatePackage;
      ttsService = ttsPackage;
      whisperService = whisperPackage;
      hybridWhisperService = hybridWhisperPackage;
      appPackage = uiPackage;
      # Packages with names expected by home-manager module
      "rt-capture-service" = capturePackage;
      "rt-playback-service" = playbackPackage;
      "rt-translate-service" = translatePackage;
      "rt-tts-service" = ttsPackage;
      "rt-whisper-service" = whisperPackage;
      "rt-whisper-hybrid-service" = hybridWhisperPackage;
      "real-time-translator" = uiPackage;
    };
  }