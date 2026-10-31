# Python dependencies of the edge app (UI + VAD + HTTP/WS clients; capture/playback run parec/pacat).
# Single list shared by nix/packages/edge.nix, nix/devshell.nix and nix/checks/edge-pytest.nix.
ps: with ps; [
  pyside6
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
