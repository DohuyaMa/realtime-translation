# Real-time Speech Translator

Real-time speech translation for live calls: microphone -> speech recognition (`rt-asr`, faster-whisper) -> LLM translation (llama worker, leased from Django, or a local `llama-server`) -> speech synthesis (`rt-tts`, Kokoro) -> PipeWire `rt_virtual_output`. A `transcribe` mode produces long-chunk transcripts without translation.

The edge (PySide6 UI, WS speech streams, translation units, HTTP clients) runs on the user's PC. `rt-asr` and `rt-tts` are independent HTTP services that can run on any host (CPU or GPU). Packaged with Nix flakes.

**Documentation (Ukrainian): [docs/USAGE.md](docs/USAGE.md)** - modes, local run, models, service and edge config, deployment, LLM lease, e2e checklist.

## Quick start

```bash
nix develop
RT_ASR_BACKEND=faster-whisper python -m src.services.asr   # :8091
python -m src.services.tts                                  # :8093
python -m src.main
```

Tests: `nix develop -c pytest tests/ -q`. Packages: `nix build .#rt-asr-cpu` (also `rt-asr-gpu`, `rt-tts-cpu`, `rt-tts-gpu`).
