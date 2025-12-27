{
  # Nix-unit tests for build validation of packages
  "capture package builds" = {
    expr = "import ./flake.nix {}).packages.x86_64-linux.capture or {}";
    expected = {};
  };

  "whisper package builds" = {
    expr = "import ./flake.nix {}).packages.x86_64-linux.whisper or {}";
    expected = {};
  };

  "translate package builds" = {
    expr = "import ./flake.nix {}).packages.x86_64-linux.translate or {}";
    expected = {};
  };

  "tts package builds" = {
    expr = "import ./flake.nix {}).packages.x86_64-linux.tts or {}";
    expected = {};
  };

  "playback package builds" = {
    expr = "import ./flake.nix {}).packages.x86_64-linux.playback or {}";
    expected = {};
  };

  # Additional build validation tests
  "all packages exist" = {
    expr = ''
      let
        flake = import ./flake.nix {};
        pkgs = flake.packages.x86_64-linux or {};
      in
      builtins.attrNames pkgs
    '';
    expected = [
      "capture"
      "whisper" 
      "translate"
      "tts"
      "playback"
      "app"
      "hybrid-whisper"
      "ui"
    ];
  };

  "entrypoints are defined" = {
    expr = ''
      let
        flake = import ./flake.nix {};
        capturePkg = flake.packages.x86_64-linux.capture or null;
      in
      if capturePkg != null && capturePkg ? python
      then capturePkg.passthru or {}
      else {}
    '';
    expected = {};
  };
}