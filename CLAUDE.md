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
python -m scripts.gen_env_table      # (inside nix develop) regenerate env tables for docs/USAGE.md
```

User documentation (Ukrainian): `docs/USAGE.md`. Specs: `docs/superpowers/specs/2026-10-22-*.md`.

## Architecture

Real-time speech translation: **edge** (PC of the user) talks over HTTP to **rt-asr** and **rt-tts** services and to llama **workers** through an app session; Django only lists the available services and their tokens (discovery, cached on disk). Local services first, cluster nodes as failover.

Modes (`services.mode`): `transcribe` (long chunks, ASR only), `translate` (phrases: ASR -> LLM -> TTS), `auto` (translate iff LLM and TTS sources exist: `local.*` or `django_urls`).

### Source layout (`src/`)

| Package | Role |
|---|---|
| `src/main.py` | Entry point: Qt app, controllers, `MainWindow` |
| `src/services/common/` | Shared service runtime: settings (`ENV_KEYS`, `TUNABLE_FIELDS`, `RELOAD_FIELDS`), `/config` (`runtime_config.py`), `/health`, `/status`, auth, model registry/store, `rt-models` CLI |
| `src/services/asr/`, `src/services/tts/` | `rt-asr` (faster-whisper, `POST /audio/transcriptions`) and `rt-tts` (Kokoro ONNX, `POST /audio/speech`, `GET /audio/voices`; both `GET /v1/models`) FastAPI services |
| `src/segmentation/` | VAD segmenter (`SegmenterConfig.translate()` / `.transcribe()`): pause-based phrase/chunk boundaries |
| `src/clients/` | HTTP clients: `asr`, `tts`, `llm`, `discovery` (Django service list + cache), endpoint `pool` (priority order, failover), `session` (worker app session, failover/return-to-local), `health` |
| `src/translation_system.py`, `src/adapters/`, `src/controller/` | Edge pipeline, adapter and controller on top of clients |
| `src/core/runtime.py` | `EdgeSettings`: `RT_EDGE_*` env > `config.yml` > defaults |
| `src/core/config.py` | `ConfigManager`: `~/.config/real-time-translator/config.yml` (keys: `config/default.yml`) |
| `src/core/preflight/pipewire.py`, `src/audio/` | PipeWire virtual sink check and routing (`pactl`) |
| `src/ui/` | PySide6 widgets and Qt controller: settings dialog (`settings_dialog.py`: allow-list + per-kind instance choice `services.use`; `service_tabs.py`: ASR/LLM/TTS tabs - per-user prompts/voice in config.yml, per-node tuning via `/config` + `/v1/models`), status panel with the instance chooser |

### Ports

`rt-asr` 8091, `rt-tts` 8093, dev `llama-server` 8080. Services listen on `RT_LISTEN_ADDRESSES` (comma list, one socket each; default `127.0.0.1`; NixOS `listenAddresses`); optional bearer token via `RT_API_TOKEN_FILE`.

### Nix (`nix/`, `flake.nix`)

- `nix/packages/` - `rtSpeech.{asr,tts}.<variant>` bundles (variants cpu/gpu, ROCm for ASR), exposed as `packages.rt-{asr,tts}-{cpu,gpu}` and `overlays.default`;
- `rt-edge` package (default, `nix run .`) - the edge UI;
- `nix/home-manager/rt-translator.nix` - `homeManagerModules.rt-translator` (`rt-translator.*` options: django.{urls,tokenFile}, local.{llm,asr,tts}, localTokenFile, allowedServices, llm.model, mode, autostart, virtualSinks -> `RT_EDGE_*` env);
- `nix/nixos/rt-speech.nix` - `services.rtSpeech.{asr,tts}` NixOS module; `nix/nixos/virtual-sinks.nix` - PipeWire null sinks `rt_virtual_input` / `rt_virtual_output`;
- `nix/lib/` - `lib.rtSpeech` (env rendering, health-check command); `nix/checks/` - flake checks; `nix/devshell.nix` - devShell.

Release is git-only: infra consumes this repo through a `github:` flake input (no `path:` inputs).

### Runtime config of services

`GET/POST /config` changes fields at runtime: TUNABLE without model reload, RELOAD with reload (rollback on failure), BOOTSTRAP only via env. Overrides persist in `RT_CONFIG_STATE`.
