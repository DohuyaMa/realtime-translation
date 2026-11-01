# Python dependencies per speech service; keep in sync with imports in src/services.
# `ps` comes from the variant's python (variant-pkgs.nix).
{ pkgs, service }:
ps:
let
  enCoreWebSm = pkgs.callPackage ./en-core-web-sm.nix { python3Packages = ps; };
  common = with ps; [
    fastapi uvicorn websockets pydantic python-multipart numpy loguru
    soundfile scipy
    huggingface-hub
  ];
  perService = {
    asr = with ps; [ faster-whisper ];
    tts = with ps; [ onnxruntime kokoro enCoreWebSm ];
    # wav2vec2 CTC on CPU torch (cache.nixos.org substitutes it)
    phonetic = with ps; [ torch transformers ];
  };
in
common ++ perService.${service}
