# Python dependencies of the edge app (UI + capture + VAD + playback + HTTP/WS clients).
# Single list shared by nix/packages/edge.nix, nix/devshell.nix and nix/checks/edge-pytest.nix.
ps: with ps; [
  pyside6
  sounddevice
  numpy
  scipy
  pysilero-vad
  pulsectl
  pyyaml
  python-dotenv
  loguru
  httpx
  websockets
]
