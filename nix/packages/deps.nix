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
    # wav2vec2 CTC: cpu = onnxruntime int8, exported once from the pinned weights (torch + onnx);
    # cuda = torch float16 (gpu variant: CUDA torch, cache.nixos-cuda.org)
    phonetic = with ps; [ torch transformers onnx onnxruntime ];
  };
in
common ++ perService.${service}
