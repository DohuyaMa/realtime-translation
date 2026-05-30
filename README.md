# Real-time Speech Translator

A NixOS-native real-time speech translation system: microphone audio → speech recognition (Whisper) → translation → TTS synthesis → virtual microphone output. Designed for live meetings (Teams, Zoom) where you speak in Ukrainian/Polish and participants hear English.

## Pipeline

```
Physical Mic
    │
    ▼
[rt-capture]  — sounddevice 48 kHz capture
    │
    ▼
[rt-whisper / rt-hybrid-whisper]  — faster-whisper (local GPU) or Wyoming (remote)
    │  recognised text (Ukrainian / Polish / auto)
    ▼
[rt-translate]  — Helsinki-NLP MarianMT or facebook/nllb-200
    │  translated text (English)
    ▼
[rt-tts]  — Kokoro-82M TTS (af_heart voice, 24 kHz)
    │  synthesised audio
    ▼
rt_virtual_output  (PipeWire null-sink)
    │
    ▼
rt_virtual_output.monitor  →  Teams / Zoom microphone input
```

All services communicate via UNIX sockets in `/run/user/$UID/rt/`.  
The UI (`translator-ui`) connects to each service and provides live recognised/translated text panels plus pipeline controls.

## Requirements

- NixOS with flakes enabled
- NVIDIA GPU (CUDA) — for real-time Whisper + TTS inference
- PipeWire audio server
- Home Manager

## Installation

Add this repository as a flake input in your NixOS configuration:

```nix
# flake.nix
inputs.realtime-translation.url = "github:your-user/real-time-transletor";
```

Enable in your Home Manager configuration:

```nix
imports = [ inputs.realtime-translation.homeManagerModules.default ];

rt-translator.enable = true;

# All settings below are optional — these are the defaults
rt-translator.whisper.model       = "medium";  # tiny/base/small/medium/large
rt-translator.whisper.device      = "cuda";
rt-translator.whisper.computeType = "float16"; # float16 / int8 / int8_float16
rt-translator.whisper.beamSize    = 5;
rt-translator.whisper.temperature = 0.0;

rt-translator.translate.sourceLang       = "uk";  # ISO 639-1
rt-translator.translate.targetLang       = "en";
rt-translator.translate.numBeams         = 4;
rt-translator.translate.repetitionPenalty = 1.2;
rt-translator.translate.maxLength        = 200;

rt-translator.tts.voice = "af_heart";  # Kokoro voice ID
rt-translator.tts.speed = 1.0;

rt-translator.wyoming.host = "localhost";
rt-translator.wyoming.port = 10300;
```

Apply:

```bash
sudo nixos-rebuild switch --flake .#your-hostname
# or
home-manager switch --flake .#your-profile
```

## Runtime Configuration

Settings changed through the UI are saved to `~/.config/real-time-translator/config.yml` and override the Nix defaults on the next service start. Priority chain:

```
config.yml (UI override)  >  Nix ExecStart arg  >  built-in fallback
```

Key config paths:

| Setting | Config key | Nix option |
|---|---|---|
| Whisper model | `models.whisper.model` | `rt-translator.whisper.model` |
| Whisper device | `models.whisper.device` | `rt-translator.whisper.device` |
| Compute type | `models.whisper.compute_type` | `rt-translator.whisper.computeType` |
| Beam size | `models.whisper.beam_size` | `rt-translator.whisper.beamSize` |
| Initial prompt | `models.whisper.initial_prompt` | — (UI only) |
| Source language | `translation.source_lang` | `rt-translator.translate.sourceLang` |
| Target language | `translation.target_lang` | `rt-translator.translate.targetLang` |
| TTS voice | `models.tts.voice` | `rt-translator.tts.voice` |
| TTS speed | `models.tts.speed` | `rt-translator.tts.speed` |

## Whisper Model Management

Models are downloaded to `~/.cache/whisper/<model>/`. To change the model at runtime:

1. Open the UI → **Service Status** panel → **Settings** (next to Whisper)
2. Select the desired model from the dropdown
3. Click **Apply** — the system will download the model if not cached, save it to config, and restart the service

Or via Settings → Models tab → select model → **Apply**.

Models and approximate sizes:

| Model | Size | VRAM | Notes |
|---|---|---|---|
| tiny | ~150 MB | ~1 GB | Fastest, lowest accuracy |
| base | ~300 MB | ~1 GB | |
| small | ~500 MB | ~2 GB | Good balance |
| **medium** | ~1.5 GB | ~5 GB | **Default** |
| large | ~3 GB | ~10 GB | Best accuracy |

## Wyoming Integration

To use a remote [Wyoming faster-whisper](https://github.com/rhasspy/wyoming-faster-whisper) server instead of local GPU inference:

1. Open UI → Service Status → Settings (Whisper)
2. Enable **"Route via Wyoming"**, set host/port
3. Click OK — the pipeline switches to `rt-hybrid-whisper` service

## Flake Structure

```
flake.nix                        # thin wrapper → flake-global/
flake-global/
├── flake.nix                    # flake-parts entry
├── home-manager-module.nix      # systemd services + options (rt-translator.*)
├── prod/
│   ├── packages.nix             # per-service buildPythonApplication
│   └── apps.nix                 # translator-ui app entry point
├── dev/
│   └── devshell.nix             # nix develop shell
└── parts/python/
    ├── common.nix               # shared Python package template
    ├── whisper.nix
    ├── hybrid-whisper.nix
    ├── translate.nix
    ├── tts.nix
    ├── capture.nix
    ├── playback.nix
    └── ui.nix
src/
├── main.py                      # Qt app entry point
├── translation_system.py        # pipeline coordinator
├── adapters/direct_adapter.py   # backend adapter (service control)
├── controller/                  # controller abstraction
├── ui/widgets/                  # PySide6 Qt6 UI
│   ├── main_window.py
│   ├── settings_dialog.py
│   ├── service_settings_dialog.py
│   ├── service_status_panel.py
│   ├── model_tab.py
│   └── status_logger.py
├── whisper/
│   ├── whisper_service.py       # local faster-whisper
│   └── hybrid_whisper_service.py # Wyoming proxy
├── translate/translate_service.py
├── tts/tts_service.py
├── capture/capture_service.py
├── playback/playback_service.py
├── common/ipc.py                # UNIX socket IPC (JSON + length-prefix)
└── core/
    ├── config.py                # ConfigManager (YAML config)
    ├── models.py                # ModelManager (cache check + download)
    └── runtime.py               # socket path resolution
```

## Development

```bash
# Enter dev shell (sets up Python deps + PipeWire virtual sinks)
nix develop

# Run the UI directly
python3 -m src.main

# Build a specific package
nix build .#whisper
nix build .#ui

# Run tests
pytest tests/
```

## Service Management

```bash
# Status of all services
systemctl --user status 'rt-*'

# Restart a service (picks up config.yml changes)
systemctl --user restart rt-whisper

# Restart all pipeline services
systemctl --user restart rt-whisper rt-translate rt-tts rt-capture rt-playback

# Live logs
journalctl --user -u rt-whisper -f
journalctl --user -u rt-translate -f
journalctl --user -u rt-app -f

# Check socket activation
systemctl --user list-sockets | grep rt-
```

## Troubleshooting

**Pipeline produces no output:**
```bash
# Check all services are active
systemctl --user is-active rt-whisper rt-translate rt-tts rt-capture rt-playback

# Check logs for errors
journalctl --user -u rt-whisper -n 50 --no-pager
journalctl --user -u rt-translate -n 50 --no-pager
```

**Whisper service fails to start:**
- Check that the model is downloaded: `ls ~/.cache/whisper/`
- Check VRAM availability: `nvidia-smi`
- Try a smaller model via the UI Settings panel

**PipeWire virtual sinks missing:**
```bash
systemctl --user restart rt-virtual-sinks
pactl list sinks short | grep rt_
```

**Translation always fails (broken pipe):**
- The translate service may have restarted — use UI → **Reconnect IPC** button
- Or restart the UI app: `systemctl --user restart rt-app`

## License

[GNU Affero General Public License v3.0](LICENSE)
