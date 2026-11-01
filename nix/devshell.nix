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
        torch transformers
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
    # Dev-only pactl equivalent of lib.rtSpeech.virtualDevicesConf (the deployed
    # devices come from the Home Manager / NixOS drop-in): pipewire-pulse cannot
    # create a loopback sink->source pair, so the microphone is a remap of the
    # output sink's monitor - same node names, same audio path. Spaces in a
    # pipewire-pulse property value must be backslash-escaped.
    if command -v pactl >/dev/null && pactl info >/dev/null 2>&1; then
      rt_dev() {  # <list: sinks|sources> <node name> <pactl load-module args...>
        local kind=$1 name=$2; shift 2
        pactl list "$kind" short | cut -f2 | grep -qx "$name" && return 0
        pactl load-module "$@" >/dev/null \
          || echo "nix develop: could not create PipeWire $name (pactl load-module $1)" >&2
      }
      rt_dev sinks rt_virtual_input module-null-sink sink_name=rt_virtual_input \
        sink_properties=device.description=RT-Virtual-Input
      rt_dev sinks rt_virtual_output module-null-sink sink_name=rt_virtual_output \
        'sink_properties=device.description="RT\ Переклад\ →\ мікрофон"'
      rt_dev sources rt_virtual_mic module-remap-source master=rt_virtual_output.monitor \
        source_name=rt_virtual_mic 'source_properties=device.description="RT\ Переклад\ (мікрофон)"'
      unset -f rt_dev
    fi
  '';
}
