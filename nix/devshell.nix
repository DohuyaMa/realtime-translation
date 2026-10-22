{ pkgs }:
let
  py = pkgs.python313;
  enCoreWebSm = pkgs.callPackage ./packages/en-core-web-sm.nix { python3Packages = py.pkgs; };
in
pkgs.mkShell {
  packages = [
    (py.withPackages (ps:
      (import ./packages/edge-deps.nix ps)
      ++ (with ps; [
        # speech services (cpu) — service code runs from here in dev
        fastapi uvicorn pydantic python-multipart httpx
        faster-whisper ctranslate2 huggingface-hub
        kokoro onnxruntime soundfile enCoreWebSm
        # tests / tooling
        pytest pytest-asyncio pytest-timeout black flake8 mypy
      ])))
    pkgs.pipewire
    pkgs.pulseaudio
    pkgs.alsa-utils
    pkgs.espeak-ng
    pkgs.curl
    pkgs.jq
  ];
  shellHook = ''
    export HF_HOME="$HOME/.cache/huggingface"
    if command -v pactl >/dev/null && pactl info >/dev/null 2>&1; then
      pactl list sinks short | grep -q rt_virtual_input \
        || pactl load-module module-null-sink sink_name=rt_virtual_input sink_properties=device.description=RT-Virtual-Input >/dev/null || true
      pactl list sinks short | grep -q rt_virtual_output \
        || pactl load-module module-null-sink sink_name=rt_virtual_output sink_properties=device.description=RT-Virtual-Output >/dev/null || true
    fi
  '';
}
