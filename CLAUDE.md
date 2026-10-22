# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build and Run Commands

Python deps come only from Nix (no pip/venv).

```bash
nix develop                          # devShell (nix/devshell.nix), PipeWire virtual sinks
python -m src.main                   # edge UI
python -m src.services.asr           # rt-asr (:8091); RT_ASR_BACKEND=faster-whisper|fake
python -m src.services.tts           # rt-tts (:8093); RT_TTS_BACKEND=kokoro|fake
nix develop -c pytest tests/ -q      # tests
nix build .#rt-asr-cpu               # also rt-asr-gpu, rt-tts-cpu, rt-tts-gpu
black src/ tests/ && flake8 src/ tests/ && mypy src/
python -m scripts.gen_env_table      # regenerate env tables for docs/USAGE.md
```

User documentation (Ukrainian): `docs/USAGE.md`. Specs: `docs/superpowers/specs/2026-10-22-*.md`.

## Architecture

Real-time speech translation: **edge** (PC of the user) talks over HTTP to **rt-asr** and **rt-tts** services and to an LLM worker obtained via a **lease** from Django (or a local OpenAI-compatible `llama-server` in dev mode).

Modes (`services.mode`): `transcribe` (long chunks, ASR only), `translate` (phrases: ASR -> LLM -> TTS), `auto` (translate iff an LLM source and `tts_urls` are set).

### Source layout (`src/`)

| Package | Role |
|---|---|
| `src/main.py` | Entry point: Qt app, controllers, `MainWindow` |
| `src/services/common/` | Shared service runtime: settings (`ENV_KEYS`, `TUNABLE_FIELDS`, `RELOAD_FIELDS`), `/config` (`runtime_config.py`), `/health`, `/status`, auth, model registry/store, `rt-models` CLI |
| `src/services/asr/`, `src/services/tts/` | `rt-asr` (faster-whisper, `POST /audio/transcriptions`) and `rt-tts` (Kokoro ONNX, `POST /audio/speech`, `GET /audio/voices`) FastAPI services |
| `src/segmentation/` | VAD segmenter (`SegmenterConfig.translate()` / `.transcribe()`): pause-based phrase/chunk boundaries |
| `src/clients/` | HTTP clients: `asr`, `tts`, `llm`, endpoint `pool` (least-pending), `lease` (Django worker lease: acquire/heartbeat/release), `health` |
| `src/translation_system.py`, `src/adapters/`, `src/controller/` | Edge pipeline, adapter and controller on top of clients |
| `src/core/runtime.py` | `EdgeSettings`: `RT_EDGE_*` env > `config.yml` > defaults |
| `src/core/config.py` | `ConfigManager`: `~/.config/real-time-translator/config.yml` (keys: `config/default.yml`) |
| `src/core/preflight/pipewire.py`, `src/audio/` | PipeWire virtual sink check and routing (`pactl`) |
| `src/ui/` | PySide6 widgets and Qt controller (endpoints, LLM lease, segmentation settings) |

### Ports

`rt-asr` 8091, `rt-tts` 8093, dev `llama-server` 8080. Services bind `127.0.0.1` by default (`RT_HOST`); optional bearer token via `RT_API_TOKEN_FILE`.

### Nix (`nix/`, `flake.nix`)

- `nix/packages/` - `rtSpeech.{asr,tts}.<variant>` bundles (variants cpu/gpu, ROCm for ASR), exposed as `packages.rt-{asr,tts}-{cpu,gpu}` and `overlays.default`;
- `nix/nixos/rt-speech.nix` - `services.rtSpeech.{asr,tts}` NixOS module; `nix/nixos/virtual-sinks.nix` - PipeWire null sinks `rt_virtual_input` / `rt_virtual_output`;
- `nix/lib/` - `lib.rtSpeech` (env rendering, health-check command); `nix/checks/` - flake checks; `nix/devshell.nix` - devShell.

Release is git-only: infra consumes this repo through a `github:` flake input (no `path:` inputs).

### Runtime config of services

`GET/POST /config` changes fields at runtime: TUNABLE without model reload, RELOAD with reload (rollback on failure), BOOTSTRAP only via env. Overrides persist in `RT_CONFIG_STATE`.
