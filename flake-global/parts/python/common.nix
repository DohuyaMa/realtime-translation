# This file exists to create the directory structure
{ pkgs }:

{
  # Common Python dependencies for all services
  commonDeps = pythonPkgs: with pythonPkgs; [
    pyyaml
    python-dotenv
    loguru
    pyaudio
    numpy
    sounddevice
    soundfile
    librosa
    pulsectl
  ];
}
