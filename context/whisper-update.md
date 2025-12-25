Добре дай повністю рекомендації що додати у флейк і як має бути архітектурно правильно для проекту
В даний момент працюємо в dev - тому в даний момент все потрібно стартувати після nix develop. Для пізнішої імплементації створено модулі systemd.
Також створений UI для тестування і вибору пристроїв - вже працює
{
  description = "Real-time speech translation system";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";
    flake-utils.url = "github:numtide/flake-utils";
    home-manager = {
      url = "github:nix-community/home-manager";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, flake-utils, home-manager }:
    let
      # Define the Home Manager module separately (not in eachSystem)
      rtTranslatorModule = { config, pkgs, ... }:
        let
          # Python packages
          pythonPackages = pkgs.python313Packages;

    # Required packages
          kokoroPackage = pythonPackages.kokoro;

    # Create proper Python environment for runtime
          pythonEnv = pkgs.python313.withPackages (ps: with ps; [
            # Core dependencies
            pyaudio
            numpy
            sounddevice

    # AI and ML
            torch
            transformers
            openai-whisper
            onnxruntime
            pyside6

    # Audio processing
            soundfile
            librosa
            pulsectl

    # Utilities
            pyyaml
            python-dotenv
            loguru

    # Kokoro TTS dependencies
            kokoroPackage
          ]);

    # Runtime wrapper for services
          serviceWrapper = name: modulePath: pkgs.writeShellApplication {
            name = "rt-${name}-service";
            runtimeInputs = [ pythonEnv pkgs.coreutils ];
            text = ''
              export PYTHONPATH="${pythonEnv}/lib/python3.12/site-packages:$PYTHONPATH"
              exec ${pythonEnv.interpreter} -m ${modulePath} "$@"
            '';
          };

    # Create service wrappers
          captureService = serviceWrapper "capture" "src.capture.capture_service";
          playbackService = serviceWrapper "playback" "src.playback.playback_service";
          translateService = serviceWrapper "translate" "src.translate.translate_service";
          ttsService = serviceWrapper "tts" "src.tts.tts_service";
          whisperService = serviceWrapper "whisper" "src.whisper.whisper_service";
        in
        {
          options.rt-translator = {
            enable = pkgs.lib.mkEnableOption "Real-time speech translation system";
          };

    config = pkgs.lib.mkIf config.rt-translator.enable {
            # Include the package in home.packages for easy access
            home.packages = [
              pythonEnv
              # Runtime wrappers
              captureService
              playbackService
              translateService
              ttsService
              whisperService
              # Pipewire utilities
              pkgs.pipewire
              pkgs.pulseaudio  # for pactl
            ];

    # Configure systemd user services
            systemd.user.services = {
              "rt-capture" = {
                description = "RT-Capture-service";
                requires = [ "rt-capture.socket" ];
                after = [ "rt-capture.socket" ];
                path = [ pythonEnv ];
                serviceConfig = {
                  Type = "simple";
                  ExecStart = "${captureService}/bin/rt-capture-service --socket-path %t/rt-capture.sock";
                  Restart = "always";
                  RestartSec = 5;
                  Environment = [
                    "PYTHONPATH=${pythonEnv}/${pythonEnv.sitePackages}"
                  ];
                };
                install = {
                  WantedBy = [ "default.target" ];
                  Also = [ "rt-capture.socket" ];
                };
              };

    "rt-playback" = {
                description = "RT-Playback-service";
                requires = [ "rt-playback.socket" ];
                after = [ "rt-playback.socket" ];
                path = [ pythonEnv ];
                serviceConfig = {
                  Type = "simple";
                  ExecStart = "${playbackService}/bin/rt-playback-service --socket-path %t/rt-playback.sock";
                  Restart = "always";
                  RestartSec = 5;
                  Environment = [
                    "PYTHONPATH=${pythonEnv}/${pythonEnv.sitePackages}"
                  ];
                };
                install = {
                  WantedBy = [ "default.target" ];
                  Also = [ "rt-playback.socket" ];
                };
              };

    "rt-translate" = {
                description = "RT-Translate-service";
                requires = [ "rt-translate.socket" ];
                after = [ "rt-translate.socket" ];
                path = [ pythonEnv ];
                serviceConfig = {
                  Type = "simple";
                  ExecStart = "${translateService}/bin/rt-translate-service --socket-path %t/rt-translate.sock";
                  Restart = "always";
                  RestartSec = 5;
                  Environment = [
                    "PYTHONPATH=${pythonEnv}/${pythonEnv.sitePackages}"
                  ];
                };
                install = {
                  WantedBy = [ "default.target" ];
                  Also = [ "rt-translate.socket" ];
                };
              };

    "rt-tts" = {
                description = "RT-TTS-service";
                requires = [ "rt-tts.socket" ];
                after = [ "rt-tts.socket" ];
                path = [ pythonEnv ];
                serviceConfig = {
                  Type = "simple";
                  ExecStart = "${ttsService}/bin/rt-tts-service --socket-path %t/rt-tts.sock";
                  Restart = "always";
                  RestartSec = 5;
                  Environment = [
                    "PYTHONPATH=${pythonEnv}/${pythonEnv.sitePackages}"
                  ];
                };
                install = {
                  WantedBy = [ "default.target" ];
                  Also = [ "rt-tts.socket" ];
                };
              };

    "rt-whisper" = {
                description = "RT-Whisper-service";
                requires = [ "rt-whisper.socket" ];
                after = [ "rt-whisper.socket" ];
                path = [ pythonEnv ];
                serviceConfig = {
                  Type = "simple";
                  ExecStart = "${whisperService}/bin/rt-whisper-service --socket-path %t/rt-whisper.sock";
                  Restart = "always";
                  RestartSec = 5;
                  Environment = [
                    "PYTHONPATH=${pythonEnv}/${pythonEnv.sitePackages}"
                  ];
                };
                install = {
                  WantedBy = [ "default.target" ];
                  Also = [ "rt-whisper.socket" ];
                };
              };

    "rt-app" = {
                description = "Real-time Translator Application";
                after = [ "graphical-session.target" "pipewire.service" ];
                wants = [ "graphical-session.target" ];
                path = [ pythonEnv pkgs.pipewire pkgs.pulseaudio ];
                serviceConfig = {
                  Type = "simple";
                  ExecStart = "${pythonEnv.interpreter} -m src.main";
                  Restart = "on-failure";
                  RestartSec = 5;
                  Environment = [
                    "PYTHONPATH=${pythonEnv}/${pythonEnv.sitePackages}"
                  ];
                };
                install = {
                  WantedBy = [ "default.target" ];
                };
              };
            };

    # Ensure pipewire is properly configured in the user environment
            xdg.configFile."pipewire/pipewire.conf.d/30-rt-virtual-sinks.conf".text = ''
              context.modules = [
                {
                  name = libpipewire-module-null-sink
                  args = {
                    node.name = "rt_virtual_input"
                    node.description = "RT-Virtual-Input"
                    media.class = "Audio/Sink"
                    stream.props = { audio.position = [ FL FR ]; }
                  }
                }
                {
                  name = libpipewire-module-null-sink
                  args = {
                    node.name = "rt_virtual_output"
                    node.description = "RT-Virtual-Output"
                    media.class = "Audio/Sink"
                    stream.props = { audio.position = [ FL FR ]; }
                  }
                }
              ]
            '';

    # Configure systemd user sockets
            systemd.user.sockets = {
              "rt-capture" = {
                description = "RT-Capture-socket";
                wantedBy = [ "sockets.target" ];
                socketConfig = {
                  ListenStream = "%t/rt-capture.sock";
                  SocketMode = "0660";
                };
              };

    "rt-playback" = {
                description = "RT-Playback-socket";
                wantedBy = [ "sockets.target" ];
                socketConfig = {
                  ListenStream = "%t/rt-playback.sock";
                  SocketMode = "0660";
                };
              };

    "rt-translate" = {
                description = "RT-Translation-socket";
                wantedBy = [ "sockets.target" ];
                socketConfig = {
                  ListenStream = "%t/rt-translate.sock";
                  SocketMode = "0660";
                };
              };

    "rt-tts" = {
                description = "RT-TTS-socket";
                wantedBy = [ "sockets.target" ];
                socketConfig = {
                  ListenStream = "%t/rt-tts.sock";
                  SocketMode = "0660";
                };
              };

    "rt-whisper" = {
                description = "RT-Whisper-socket";
                wantedBy = [ "sockets.target" ];
                socketConfig = {
                  ListenStream = "%t/rt-whisper.sock";
                  SocketMode = "0660";
                };
              };
            };
          };
        };
in
{

# Home Manager module

  homeManagerModules.rt-translator = rtTranslatorModule;

# NixOS module for production deployment

  nixosModules.virtual-sinks = import ./nixosModules/virtual-sinks.nix;

# System-specific outputs using flake-utils

} // flake-utils.lib.eachSystem ["x86_64-linux"] (system:
  let
    pkgs = nixpkgs.legacyPackages.${system};

    # Python packages
    pythonPackages = pkgs.python313Packages;

    # Use the official kokoro package from nixpkgs
    kokoroPackage = pythonPackages.kokoro;

    # Create application package using mkDerivation instead of buildPythonPackage
    appPackage = pkgs.stdenv.mkDerivation {
      pname = "real-time-translator";
      version = "0.1.0";
      src = ./.;

    nativeBuildInputs = with pkgs; [ makeWrapper python313 ];
      propagatedBuildInputs = with pythonPackages; [
        # Core dependencies
        pyaudio
        numpy
        sounddevice

    # AI and ML
        torch
        transformers
        openai-whisper
        onnxruntime
        pyside6

    # Audio processing
        # Audio processing
        soundfile
        librosa
        pulsectl

    # Utilities
        pyyaml
        python-dotenv
        loguru

    # Kokoro TTS dependencies
        kokoroPackage
      ];

    buildPhase = "true"; # Skip build phase

    installPhase = ''
        runHook preInstall
        mkdir -p$out/bin $out/share/real-time-translator

    # Copy source files, excluding result symlink and .git
        cp -r$src/* $out/share/real-time-translator/
        rm -rf $out/share/real-time-translator/.git
        # Remove result symlink if it exists to avoid broken symlink errors
        rm -f $out/share/real-time-translator/result

    # Create wrapper script for the application
        makeWrapper${pythonPackages.python.interpreter} $out/bin/real-time-translator
    --prefix PYTHONPATH : "$out/share/real-time-translator"
    --add-flags "-m src.main"
      '';

    doInstallCheck = false;
    };

    # System dependencies for devShell
    systemPackages = with pkgs; [
      # Core system tools
      just

    # Qt dependencies for GUI (moved from systemd services)
      qt6.qtbase
      qt6.qtwayland
      xorg.libX11
      xorg.libXext
      xorg.libXrender
      xorg.libXrandr
      xorg.libXfixes
      libGL

    # Development tools
      nodejs
      pnpm
      python313
      python313Packages.pip
      python313Packages.virtualenv

    # Libraries needed for audio processing
      libffi
      openssl
      zlib

    # Additional system libraries
      gcc
      gnumake
      pkg-config
      ninja

    # Audio tools (using pipewire instead of conflicting pulseaudio)
      pipewire
      pulseaudio      # for pactl compatibility
      alsa-utils
    ];
  in
  {
    devShells.default = pkgs.mkShell {
      buildInputs = systemPackages ++ (with pythonPackages; [
        # Individual packages instead of the combined pythonEnv that needs building
        pyaudio
        numpy
        sounddevice
        torch
        transformers
        openai-whisper
        onnxruntime
        pyside6
        soundfile
        librosa
        pulsectl
        pyyaml
        python-dotenv
        loguru
        kokoroPackage
        pytest
      ]);

    # Environment variables
      PIP_DISABLE_PIP_VERSION_CHECK = "1";
      HF_HOME = "$HOME/.cache/huggingface";
      TRANSFORMERS_CACHE = "$HOME/.cache/transformers";
      HF_HUB_CACHE = "$HOME/.cache/huggingface/hub";
             # Setup hooks
             shellHook = ''
               export PYTHONPATH="$PWD:$PYTHONPATH"
               export HF_HOME="$HOME/.cache/huggingface"
               export TRANSFORMERS_CACHE="$HOME/.cache/transformers"
               export HF_HUB_CACHE="$HOME/.cache/huggingface/hub"

    # Create cache directories
               mkdir -p "$HOME/.cache/huggingface"
               mkdir -p "$HOME/.cache/transformers"
               mkdir -p "$HOME/.cache/huggingface/hub"

    # Restart PipeWire and create virtual sinks for devShell
               # (devShell is isolated and doesn't use systemd user services)
               systemctl --user restart pipewire pipewire-pulse || true
               pactl load-module module-null-sink sink_name=rt_virtual_input sink_properties=device.description="RT-Virtual-Input" || true
               pactl load-module module-null-sink sink_name=rt_virtual_output sink_properties=device.description="RT-Virtual-Output" || true

    echo "Real-time Translator development environment ready!"
               echo "Use 'python3 -m src.main' to start the application"
               echo ""
               echo "PipeWire virtual sinks have been created:"
               echo "  - rt_virtual_input (RT-Virtual-Input)"
               echo "  - rt_virtual_output (RT-Virtual-Output)"
               echo "These are available for audio routing in the development environment."
             '';
    };

    packages.default = appPackage;

    apps.default = {
      type = "app";
      program = "${appPackage}/bin/real-time-translator";
    };
  }
);
}

Нижче — **цілісна, правильна рекомендація** для твого проєкту  **без переписування всього** , але з чітким розмежуванням  **що лишаємо, що прибираємо, що змінюємо** , і **як це має виглядати архітектурно** у dev (`nix develop`) і далі у production (`systemd`).

Я буду опиратися  **конкретно на твій flake** , а не абстрактну теорію.

---

# 1. Архітектура: як має бути ПРАВИЛЬНО

## 1.1 Канонічна схема (ти вже майже тут)

```
┌────────────┐
│  PipeWire  │
└─────┬──────┘
      │ raw PCM
┌─────▼──────┐
│ rt-capture │  (audio in)
└─────┬──────┘
      │ socket
┌─────▼──────┐
│ rt-whisper │  (ASR)
│ faster-wh. │
└─────┬──────┘
      │ text segments
┌─────▼──────┐
│ rt-translate│
└─────┬──────┘
      │ text
┌─────▼──────┐
│   rt-tts   │
└─────┬──────┘
      │ audio
┌─────▼──────┐
│ rt-playback│
└────────────┘
```

 **UI** :

* не в пайплайні
* лише клієнт до сокетів
* вибір device / мови / режимів

👉 **Це вже правильно. Ти мислиш вірно.**

---

# 2. Головна проблема твого flake (ключове)

### ❌ Зараз у тебе:

* `openai-whisper`
* `speech_recognition` (раніше)
* torch + transformers **для ASR**
* ASR і UI в одному pythonEnv

### ✅ Має бути:

* **faster-whisper (CTranslate2)** як єдиний ASR
* **NO openai-whisper**
* **NO speech_recognition**
* ASR = окремий, легкий сервіс

---

# 3. Що КОНКРЕТНО змінити у твоєму flake

## 3.1 Python ASR стек (критично)

### 🔴 Прибрати ЗАВЖДИ

```nix
openai-whisper
speech_recognition
```

Вони  **не повинні існувати в проєкті взагалі** .

---

### 🟢 Додати

У `pythonEnv` і `devShell`:

```nix
faster-whisper
ctranslate2
```

> `faster-whisper` автоматично підтягне CTranslate2, але в nix краще явно.

---

## 3.2 torch / transformers — тільки якщо реально треба

### Для ASR:

❌ `transformers` не потрібен
❌ `torch` потрібен  **тільки як backend** , не як Whisper

Рекомендація:

* залиш `torch`
* **викинь `transformers`** , якщо він не використовується UI або іншими модулями

---

## 3.3 Python environment — ти робиш ПРАВИЛЬНО

Ось це  **добре** :

```nix
python313.withPackages (...)
serviceWrapper
rt-*-service
```

Це правильний патерн.

---

# 4. Dev-режим (`nix develop`) — як має бути

## 4.1 Що ти робиш правильно

✔ `shellHook`
✔ старт PipeWire
✔ створення null-sink
✔ ручний запуск сервісів

Це  **OK для dev** .

---

## 4.2 Що варто змінити

### ❌ НЕ рестартуй pipewire кожен раз

```bash
systemctl --user restart pipewire
```

👉 У dev це часто ламає сесію.

### ✅ Замість цього

* тільки перевірка
* тільки створення sink, якщо не існує

```bash
pactl list short sinks | grep -q rt_virtual_input || \
  pactl load-module module-null-sink sink_name=rt_virtual_input
```

---

## 4.3 Dev-режим: як стартувати пайплайн

### РЕКОМЕНДАЦІЯ

У dev  **НЕ systemd** , а:

```bash
rt-capture-service &
rt-whisper-service &
rt-translate-service &
rt-tts-service &
rt-playback-service &
```

UI → підʼєднується до сокетів.

✔ Це правильно
✔ Це контрольовано
✔ Це дебажиться

---

# 5. Systemd (ти все зробив правильно)

## 5.1 Сокети + сервіс — ІДЕАЛЬНО

```nix
systemd.user.sockets
systemd.user.services
```

✔ socket activation
✔ restart
✔ isolation

👉 **Нічого архітектурно міняти не треба**

---

## 5.2 Єдине уточнення

### ASR (whisper) сервіс

РЕКОМЕНДОВАНО:

* `Nice=-5`
* `CPUSchedulingPolicy=rr`
* `IOSchedulingClass=best-effort`

Це реально зменшує latency.

---

# 6. ASR-модуль: як він МАЄ виглядати

## 6.1 `rt-whisper-service` (логіка)

* stdin / socket → raw PCM
* `faster-whisper`
* output:

```json
{
  "text": "…",
  "start": 1.23,
  "end": 2.91,
  "final": true
}
```

❌ НІ:

* UI
* файлів
* device selection

---

# 7. Фінальний список пакетів (еталон)

## 7.1 Мінімум для runtime

```nix
# audio
pipewire
pulseaudio  # pactl only
sounddevice
soundfile

# ASR
faster-whisper
ctranslate2
torch

# translate
argos-translate / translateLocally

# TTS
kokoro
onnxruntime

# infra
loguru
pyyaml
pulsectl
```

---

## 7.2 Що НЕ повинно бути

```
speech_recognition
openai-whisper
nltk
transformers (якщо не треба)
```

---

# 8. Загальний вердикт

### 🔥 Архітектурно

Ти  **вже зробив 80% правильно** .

### 🔧 Потрібно виправити:

1. **ASR стек** (тільки faster-whisper)
2. прибрати legacy бібліотеки
3. трохи спростити dev shell
4. чітко відокремити UI від pipeline

---
