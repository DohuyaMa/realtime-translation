{ python3
, lib
, callPackage
}:

callPackage ./common.nix {
  pname = "translator-ui";
  src = ../../../src;

  dependencies = with python3.pkgs; [
    # UI service specific dependencies
    pyqt5
    pyside6
    pulsectl
    pyyaml
    python-dotenv
    loguru
  ];
}