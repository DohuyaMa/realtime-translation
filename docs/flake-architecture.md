# Nix Flake Architecture

## Структура

Один кореневий `flake.nix` без суб-флейків. Пакети і devShell живуть у `flake-global/` як звичайні `.nix` файли, які імпортуються напряму.

```
flake.nix                          ← єдина точка входу
flake-global/
  prod/packages.nix                ← визначає всі Python-пакети
  prod/apps.nix                    ← (не використовується напряму, збережено для довідки)
  dev/devshell.nix                 ← devShell з усіма залежностями
  home-manager-module.nix          ← HM-модуль з systemd-сервісами
  parts/python/
    common.nix                     ← шаблон buildPythonApplication
    capture.nix / playback.nix / translate.nix
    tts.nix / whisper.nix / hybrid-whisper.nix / ui.nix
nixosModules/
  virtual-sinks.nix                ← NixOS-модуль для PipeWire null sinks
```

## Inputs

| Input | Використання |
|---|---|
| `nixpkgs` → `nixpkgs-unstable` | Всі пакети |
| `flake-utils` | `eachSystem` для ітерації по системах |
| `home-manager` | Home Manager модуль |

**Видалено порівняно зі старою версією:** `flake-parts`, `nix-unit`, `flake-global` (суб-флейк).

## Outputs

```nix
flake-utils.lib.eachSystem [ "x86_64-linux" "aarch64-linux" "aarch64-darwin" "x86_64-darwin" ]
  → packages    # всі сервіси + аліаси для home-manager
  → devShells   # середовище розробки
  → apps        # translator-ui як nix run

// (system-агностик)
  → homeManagerModules.rt-translator
  → nixosModules.virtual-sinks
```

## Пакети (`packages.*`)

Кожен сервіс — окремий `buildPythonApplication` зі спільним `pyproject.toml` (весь `src/` в одному пакеті):

| Ім'я | Бінарник | Опис |
|---|---|---|
| `capture` | `translator-capture` | Захват аудіо |
| `whisper` | `translator-whisper` | Whisper STT |
| `hybrid-whisper` | `translator-hybrid-whisper` | Whisper + Wyoming proxy |
| `translate` | `translator-translate` | MarianMT переклад |
| `tts` | `translator-tts` | Kokoro TTS |
| `playback` | `translator-playback` | Відтворення аудіо |
| `ui` / `app` / `default` | `translator-ui` | PySide6 UI |

## Home Manager модуль

`homeManagerModules.rt-translator` надає:

- **systemd user сервіси** з socket-активацією: `rt-capture`, `rt-playback`, `rt-translate`, `rt-tts`, `rt-whisper`, `rt-hybrid-whisper`, `rt-app`, `rt-virtual-sinks`
- **systemd user сокети**: один `.socket` на кожен сервіс, шлях `%t/rt/*.sock`
- **xdg.configFile**: PipeWire конфіг для null sinks (`rt_virtual_input`, `rt_virtual_output`)
- **Опції конфігурації** (NixOS-стиль): `rt-translator.whisper.*`, `rt-translator.translate.*`, `rt-translator.tts.*`, `rt-translator.wyoming.*`

## NixOS модуль

`nixosModules.virtual-sinks` — системний PipeWire модуль для машин де virtual sinks потрібні на рівні системи (не home-manager).

## Відомі виправлення

### `src/models` не включався у збірку (2026-05-31)

`.gitignore` мав патерн `models/` який відповідав `src/models/` (Python-пакет з `TTSEngine`, `WhisperRecognition`). Оскільки Nix будує з git-трекінгу, `src/models/` не потрапляло у `buildPythonApplication` → `ModuleNotFoundError: No module named 'src.models'`.

**Виправлення:** змінено `models/` → `/models/` у `.gitignore`, `src/models/` додано до git.

### Спрощення flake (2026-05-31)

Стара структура мала два рівні flake (`flake.nix` → `flake-global/flake.nix`) і використовувала `flake-parts` тільки для `perSystem` блоку. Замінено єдиним `flake.nix` з `flake-utils.lib.eachSystem`. Прибрано 15 вкладених inputs з `flake.lock`.
