# Інтеграція real-time-transletor у swarm-nix

> Станом на 2026-05-23. Обидва проєкти — локально на диску:
> - `~/repos/real-time-transletor`
> - `~/repos/swarm-nix`

## Структура swarm-nix (спрощено)

```
system-conf/
├── flake.nix                         # головний flake — inputs, nixosConfigurations, colmenaHive
├── common/
│   ├── nixos-base.nix               # базова конфігурація для всіх машин
│   └── nixos-config.nix             # спільні налаштування (timezone, locale, SSH...)
├── machines/
│   └── cyborg/
│       ├── system.nix               # вхідна точка: nixos-base + configuration.nix + home-manager
│       └── configuration.nix        # специфічні для машини налаштування
```

## Що надає real-time-transletor

Зовнішній flake надає:

| Output | Призначення |
|---|---|
| `packages.x86_64-linux.{capture,whisper,translate,tts,playback,hybrid-whisper,ui}` | Окремі service-пакети |
| `packages.x86_64-linux.app` | Повний PythonEnv для runtime |
| `nixosModules.virtual-sinks` | NixOS модуль для PipeWire null sinks |
| `homeManagerModules.rt-translator` | Home Manager модуль — systemd user сервіси + socket activation |

## Крок 1 — Додати input у `flake.nix`

Файл: `system-conf/flake.nix`

```nix
{
  description = "NixOS infrastructure";

  inputs = {
    # ... existing inputs (nixpkgs, colmena, flake-parts, home-manager, llm-agents) ...

    realtime-translation = {
      url = "path:/home/dmaslo/repos/real-time-transletor";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, colmena, home-manager, flake-parts, llm-agents, realtime-translation, ... }@inputs:
    # ... решта без змін ...
```

> **Чому `path:`:** Поки триває розробка — завжди береться актуальна версія. Коли стабілізується — замінити на `github:DohuyaMa/realtime-translation`.

## Крок 2 — Додати NixOS модуль virtual-sinks

Модуль `rt.audio.virtualSinks` створює два PipeWire null sinks: `rt_virtual_input` та `rt_virtual_output`.

### Варіант A — глобально для cyborg у `flake.nix`

```nix
systemModules = {
  cyborg = [
    ./machines/cyborg/system.nix
    realtime-translation.nixosModules.virtual-sinks
  ];
  # ... інші машини
};
```

Потім у `machines/cyborg/configuration.nix`:

```nix
{ config, pkgs, inputs, ... }:  # inputs має бути в аргументах

{
  # ... існуючі імпорти ...

  # Увімкнути віртуальні sinks
  rt.audio.virtualSinks.enable = true;
}
```

### Варіант B — імпорт модуля напряму в `configuration.nix`

```nix
{ config, pkgs, inputs, ... }:

{
  imports = [
    ./hardware-configuration.nix
    ./modules/hardware/default.nix
    ./modules/containers/default.nix
    ./modules/services/postgresql.nix
    ./modules/flake-services
    # Додати:
    inputs.realtime-translation.nixosModules.virtual-sinks
  ];

  rt.audio.virtualSinks.enable = true;
}
```

**Важливо:** `inputs` має бути доступний у `configuration.nix`. Він потрапляє туди через `specialArgs = { inherit inputs; }` у `nixosSystem` (вже є в `flake.nix`, рядок 67).

## Крок 3 — Додати Home Manager модуль

### У `machines/cyborg/system.nix`

```nix
{ inputs, ... }:   # inputs потрібен для доступу до realtime-translation

{
  imports = [
    ../../common/nixos-base.nix
    ./configuration.nix
  ];

  home-manager.users.dmaslo = {
    imports = [
      ./home/default.nix
      inputs.realtime-translation.homeManagerModules.rt-translator
    ];

    # Увімкнути всі сервіси перекладу
    rt-translator.enable = true;
  };
}
```

### Що це активує

**home.packages:**
- Python 3.13 + повний PythonEnv (torch, transformers, faster-whisper, pyaudio, sounddevice, pyside6, kokoro, loguru тощо)
- Окремі service-пакети: `rt-capture-service`, `rt-whisper-service` та ін.
- pipewire, pulseaudio (pactl)

**systemd user services (socket-activated):**

| Service | Socket | Команда |
|---|---|---|
| `rt-capture.service` | `rt-capture.socket` | capture-service --socket-path %t/rt-capture.sock |
| `rt-whisper.service` | `rt-whisper.socket` | whisper-service --socket-path %t/rt-whisper.sock |
| `rt-translate.service` | `rt-translate.socket` | translate-service --socket-path %t/rt-translate.sock |
| `rt-tts.service` | `rt-tts.socket` | tts-service --socket-path %t/rt-tts.sock |
| `rt-playback.service` | `rt-playback.socket` | playback-service --socket-path %t/rt-playback.sock |
| `rt-hybrid-whisper.service` | `rt-hybrid-whisper.socket` | hybrid-whisper --use-wyoming ... |
| `rt-app.service` | — | translator-ui (графічний інтерфейс) |
| `rt-virtual-sinks.service` | — | створює null sinks (oneshot) |

**Socket paths (production via systemd):** `$XDG_RUNTIME_DIR/rt-*.sock` (тобто `/run/user/$UID/rt-whisper.sock`)

**Socket paths (devShell):** `/tmp/rt-*.sock`

## Крок 4 — Оновити lock-файл

```bash
cd ~/repos/swarm-nix/system-conf
nix flake lock --update-input realtime-translation
```

## Крок 5 — Задеплоїти

```bash
# Локально
sudo nixos-rebuild switch --flake .#cyborg

# Або через Colmena
nix run .#colmena -- apply --on cyborg
```

Після деплою — `systemctl --user status rt-*.service` покаже всі сервіси.

## Альтернатива: тільки devShell, без systemd

Якщо не потрібні systemd сервіси — достатньо пакетів у `environment.systemPackages`:

```nix
{ config, pkgs, inputs, ... }:

{
  environment.systemPackages = with pkgs; [
    # ... існуючі пакети ...

    # Пакети real-time-transletor
    inputs.realtime-translation.packages.x86_64-linux.ui
    inputs.realtime-translation.packages.x86_64-linux.capture
    inputs.realtime-translation.packages.x86_64-linux.whisper
    inputs.realtime-translation.packages.x86_64-linux.translate
    inputs.realtime-translation.packages.x86_64-linux.tts
    inputs.realtime-translation.packages.x86_64-linux.playback
    inputs.realtime-translation.packages.x86_64-linux.hybrid-whisper
  ];
}
```

**Але:** це лише бінарні обгортки — для запуску `translator-ui` потрібно мати всі Python-залежності. Для dev-режиму краще використати `nix develop ~/repos/real-time-transletor`.

## Моделі: як і де кешуються

Моделі не керуються через Nix (окрім `en_core_web_sm`). Вони качаються при першому запуску відповідного сервісу.

### Список моделей

| Модель | Сервіс | Тип | Розмір | Де кешується |
|---|---|---|---|---|
| `en_core_web_sm` | TTS (kokoro → misaki G2P) | spaCy, **Nix derivation** (wheel) | ~50MB | Nix store — не треба качати |
| `hexgrad/Kokoro-82M` | TTS | HuggingFace | ~82M params | `~/.cache/huggingface/hub/` |
| `Helsinki-NLP/opus-mt-uk-en` | Translate | HuggingFace | ~300MB | `~/real-time-translator-cache/huggingface/hub/` |
| `small` (faster-whisper) | Whisper (якщо `use_wyoming: false`) | CTranslate2 | ~500MB | `~/.cache/whisper/` |

### Як скачати

**Translate:**
```bash
nix develop --command python3 -c "from transformers import pipeline; pipeline('translation', model='Helsinki-NLP/opus-mt-uk-en')"
```
Кешується в `~/real-time-translator-cache/huggingface/hub/` (налаштовано через `HF_HUB_CACHE` в translate_service та devshell).

**TTS Kokoro:**
```bash
nix develop --command python3 -c "from huggingface_hub import snapshot_download; snapshot_download('hexgrad/Kokoro-82M')"
```
Кешується в `~/.cache/huggingface/hub/`.

**Whisper local (без Wyoming):**
```bash
nix develop --command python3 -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', download_root='~/.cache/whisper/')"
```

### Важливо для swarm-nix

- **`en_core_web_sm`** — це єдина модель, яка управляється через Nix. Додана як `buildPythonPackage { format = "wheel"; }` в `flake-global/dev/devshell.nix` та `flake-global/parts/python/tts.nix`. Користувачу не треба про неї думати.
- **Всі інші моделі** качаються в `~/.cache/` при першому запуску. В systemd-режимі це станеться автоматично при старті сервісу.
- **Wyoming як альтернатива** — можна взагалі не качати `faster-whisper` модель, якщо запустити Wyoming faster-whisper сервер окремо. Для цього `use_wyoming: true` в конфігу.

### Налаштування `HF_HOME` в swarm-nix

В production (Home Manager module) шлях до кешу можна перевизначити в `systemd` сервісах через `environment`:

```nix
# home-manager-module.nix вже має:
# Environment = "HF_HOME=%h/real-time-translator-cache/huggingface"
# 
# Якщо треба змінити — змінити в flake-global/home-manager-module.nix
```

## Відомі обмеження

1. **Тільки cyborg** — інші машини можуть не мати мікрофона або PipeWire
2. **Моделі не кешуються системно** — окрім `en_core_web_sm`, всі моделі качаються при першому запуску в `~/.cache/`. Перший старт після деплою буде повільним (скачування ~1GB моделей)
3. **PythonEnv важкий** — `torch` (~2GB) + `pyside6` (~200MB). Якщо це проблема — можна використати Wyoming faster-whisper замість локальної моделі
4. **DevShell socket paths** — `/tmp/rt-*.sock` не збігаються з production `%t/rt-*.sock`. `get_runtime_config()` визначає шлях автоматично залежно від наявності файлів
