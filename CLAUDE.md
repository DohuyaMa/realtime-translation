# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build and Run Commands

Python deps come only from Nix (no pip/venv).

```bash
nix develop                          # devShell (nix/devshell.nix), PipeWire virtual sinks
python -m src.main                   # edge UI
python -m src.services.asr           # rt-asr (:8091); RT_ASR_BACKEND=faster-whisper|fake
python -m src.services.tts           # rt-tts (:8093); RT_TTS_BACKEND=kokoro|fake
python -m src.services.phonetic      # rt-phonetic (:8092); RT_PHONETIC_BACKEND=wav2vec2|fake
nix develop -c pytest tests/ -q      # tests
nix build .#rt-asr-cpu               # also rt-asr-gpu, rt-phonetic-{cpu,gpu}, rt-tts-cpu, rt-tts-gpu
black src/ tests/ && flake8 src/ tests/ && mypy src/
python -m scripts.gen_env_table      # (inside nix develop) regenerate env tables for docs/USAGE.md
```

User documentation (Ukrainian): `docs/USAGE.md`. Specs: `docs/superpowers/specs/2026-10-22-*.md`.

## Architecture

Real-time speech translation: **edge** (PC of the user) talks over HTTP to **rt-asr** and **rt-tts** services and to llama **workers** through an app session; Django only lists the available services and their tokens (discovery, cached on disk). Local services first, cluster nodes as failover.

Modes (`services.mode`): `transcribe` (stream units as text, ASR only), `translate` (simultaneous: stream units -> LLM -> TTS), `auto` (translate iff LLM and TTS sources exist: `local.*` or `django_urls`).

### Source layout (`src/`)

| Package | Role |
|---|---|
| `src/main.py` | Entry point: Qt app, controllers, `MainWindow` |
| `src/services/common/` | Shared service runtime: settings (`ENV_KEYS`, `TUNABLE_FIELDS`, `RELOAD_FIELDS`), `/config` (`runtime_config.py`), `/health`, `/status`, auth, model registry/store, `rt-models` CLI |
| `src/services/asr/`, `src/services/tts/` | `rt-asr` (faster-whisper, `POST /audio/transcriptions`) and `rt-tts` (Kokoro ONNX, `POST /audio/speech`, `GET /audio/voices`; both `GET /v1/models`) FastAPI services |
| `src/services/phonetic/` | `rt-phonetic` (character-level wav2vec2 CTC, no dictionary; device switchable at runtime (`/config`, «Фонетика» tab): cpu = onnxruntime int8 (default; ONNX exported once by `fetch()` into `<model dir>/onnx/`, `onnx_export.py`), no VRAM; cuda = torch float16, on cyborg 0.06-0.09 s per 10 s audio, ~1 GiB VRAM; rt-asr request shape, answer = lowercase letters as heard + word times; `RT_MODEL` = ONE model of one language, `uk-wav2vec2-stress`; other language -> 409 `model_language_mismatch`). Edge translate mode, per unit: `core/crosscheck.py` letter gate (drops whisper hallucinations) and "condition F" translation - SOUND (letters) primary, whisper TEXT only spelling/numbers; `services.use.phonetic` = auto/node/off; evidence `context/phonetic-bench-2026-11-01.md`; offline replay `scripts/replay_session.py`; opt-in recording `core/session_record.py`, `services.record_session` |
| `src/core/simul.py`, `src/clients/stream.py`, `src/audio/{resample,level,trim}.py` | Stream pipeline: WS stream client (rt-asr), `SourceLedger` units (sentence/endpoint/clause/20 words), edge level normalization, TTS edge-silence trim |
| `src/clients/` | HTTP clients: `asr`, `phonetic`, `tts`, `llm`, `discovery` (Django service list + cache), endpoint `pool` (priority order, failover), `session` (worker app session, failover/return-to-local), `health` |
| `src/translation_system.py`, `src/adapters/`, `src/controller/` | Edge pipeline, adapter and controller on top of clients |
| `src/core/runtime.py` | `EdgeSettings`: `RT_EDGE_*` env > `config.yml` > defaults |
| `src/core/config.py` | `ConfigManager`: `~/.config/real-time-translator/config.yml` (keys: `config/default.yml`) |
| `src/core/preflight/pipewire.py`, `src/audio/` | PipeWire virtual device check and routing (`pactl`); capture records the chosen source via `parec` (`PulseCapture`), playback goes to the chosen sink(s) via `pacat` (`play_to_sinks`); system defaults are never changed |
| `src/ui/` | PySide6 widgets and Qt controller: settings dialog (`settings_dialog.py`: allow-list + per-kind instance choice `services.use`; `service_tabs.py`: ASR/Фонетика/LLM/TTS tabs - per-user prompts/voice in config.yml, per-node tuning via `/config` + `/v1/models`; LLM tab: the llama worker's loaded model via `clients/llm_worker.py` - `GET /status`, `GET /models`, `POST /models/load`, `DELETE /models/unload`), status panel with the instance chooser; kill switch (`DirectAdapter.standby/resume`) and the drawn tray icon (`widgets/tray_icon.py`) |

### Ports

`rt-asr` 8091, `rt-phonetic` 8092, `rt-tts` 8093, dev `llama-server` 8080. Services listen on `RT_LISTEN_ADDRESSES` (comma list, one socket each; default `127.0.0.1`; NixOS `listenAddresses`); optional bearer token via `RT_API_TOKEN_FILE`.

### Nix (`nix/`, `flake.nix`)

- `nix/packages/` - `rtSpeech.{asr,phonetic,tts}.<variant>` bundles (variants cpu/gpu, ROCm for ASR), exposed as `packages.rt-{asr,phonetic,tts}-{cpu,gpu}` and `overlays.default`;
- `rt-edge` package (default, `nix run .`) - the edge UI;
- `nix/home-manager/rt-translator.nix` - `homeManagerModules.rt-translator` (`rt-translator.*` options: django.{urls,tokenFile}, local.{llm,asr,tts}, localTokenFile, allowedServices, mode, autostart, virtualSinks -> `RT_EDGE_*` env);
- `nix/nixos/rt-speech.nix` - `services.rtSpeech.{asr,phonetic,tts}` NixOS module; `nix/nixos/virtual-sinks.nix` - PipeWire devices (`lib.rtSpeech.virtualDevicesConf`, shared with the HM module): null sink `rt_virtual_input`, loopback `rt_virtual_output` (sink) -> `rt_virtual_mic` (source for other apps);
- `nix/lib/` - `lib.rtSpeech` (env rendering, health-check command); `nix/checks/` - flake checks; `nix/devshell.nix` - devShell.

Release is git-only: infra consumes this repo through a `github:` flake input (no `path:` inputs).

### Runtime config of services

`GET/POST /config` changes fields at runtime: TUNABLE without model reload, RELOAD with reload (old model unloaded first; a failed load leaves state `error`, no rollback; `POST /model/{load,unload}` are the explicit buttons), BOOTSTRAP only via env. Overrides persist in `RT_CONFIG_STATE`.
