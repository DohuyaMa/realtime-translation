# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build and Run Commands

```bash
# Enter the Nix development shell (sets up all Python deps + PipeWire virtual sinks)
nix develop

# Run the UI application
python3 -m src.main

# Build a specific Nix package
nix build .#capture     # or whisper, translate, tts, playback, hybrid-whisper, ui

# Run the UI directly via Nix app
nix run .

# Run all tests
pytest tests/

# Run a single test file
pytest tests/test_pipeline.py

# Format / lint / type check
black src/ tests/
flake8 src/ tests/
mypy src/
```

## Architecture

The system is a **real-time speech translation pipeline**: audio capture → speech recognition (Whisper) → translation → TTS → audio playback. Built with Python 3.13 + PySide6 (Qt6) UI, packaged via Nix flakes.

### Nix flake structure

- **`flake.nix`** – thin wrapper that delegates everything to `flake-global/`
- **`flake-global/`** – the actual flake used by `flake-parts`. Contains:
  - `prod/packages.nix` – imports per-service packages from `parts/python/*.nix`
  - `prod/apps.nix` – declares the `translator-ui` app entry point
  - `dev/devshell.nix` – devShell with all Python deps + PipeWire virtual sinks setup
  - `home-manager-module.nix` – Home Manager module to deploy all services as systemd user units
- **`parts/python/common.nix`** – shared `buildPythonApplication` template used by every service
- **`nixosModules/virtual-sinks.nix`** – NixOS module to set up PipeWire null sinks at system level

### Python source layout (`src/`)

| Package | Role |
|---|---|
| `src/main.py` | Entry point: creates Qt app, `ConcreteTranslatorController`, `UIController`, `MainWindow` |
| `src/translation_system.py` | Core coordinator; holds IPC clients for each microservice |
| `src/adapters/direct_adapter.py` | **DirectAdapter** – wraps `TranslationSystem`; the only supported adapter mode |
| `src/pipeline/orchestrator.py` | Spawns each service as a subprocess; used for IPC mode (not default) |
| `src/controller/translator_controller.py` | Backend controller abstraction |
| `src/ui/controller/ui_controller.py` | Qt-side controller that bridges UI widgets ↔ backend |
| `src/ui/widgets/` | All PySide6 widgets: `MainWindow`, `SettingsDialog`, `ServiceStatusPanel`, etc. |
| `src/capture/`, `src/whisper/`, `src/translate/`, `src/tts/`, `src/playback/` | Independent microservices; each exposes a Unix socket via `--socket-path` |
| `src/common/ipc.py` | IPC client/server over Unix sockets (JSON messages) |
| `src/core/config.py` | `ConfigManager` – YAML config at `~/.config/real-time-translator/config.yml` |
| `src/core/runtime.py` | Socket path resolution (`get_runtime_config()`) |
| `src/core/preflight/pipewire.py` | PipeWire virtual sink preflight check (runs at startup) |
| `src/audio/routing.py` | `AudioRouter` – wraps `pactl` for device management |
| `src/models/` | `WhisperRecognition`, `TTSEngine` (Kokoro) |

### Service communication

In **direct mode** (default), `DirectAdapter` wraps `TranslationSystem` which connects to each microservice via a Unix socket IPC client. Socket paths come from `get_runtime_config()`.

In production (Home Manager), each service runs as a **systemd user socket-activated unit** (`rt-capture`, `rt-whisper`, `rt-translate`, `rt-tts`, `rt-playback`, `rt-hybrid-whisper`).

### Audio infrastructure

Requires two PipeWire null sinks: `rt_virtual_input` and `rt_virtual_output`. These are created:
- At devShell entry (via `shellHook`)
- By `rt-virtual-sinks` systemd user service in production
- By `nixosModules.virtual-sinks` at the NixOS level

`DirectAdapter` runs a `PipeWirePreflight` check on startup and raises if sinks are missing.

### Wyoming integration

The `hybrid-whisper` service can proxy to a [Wyoming faster-whisper](https://github.com/rhasspy/wyoming-faster-whisper) server instead of running Whisper locally. Toggled via `--use-wyoming` flag or `wyoming.use_wyoming` config key. Default Wyoming port: 10300.

### Configuration

Runtime config lives at `~/.config/real-time-translator/config.yml`. The `config/default.yml` in the repo documents all available keys. `ConfigManager.get()` / `.set()` use dot-notation (e.g. `wyoming.host`).
