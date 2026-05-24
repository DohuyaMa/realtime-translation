# Fix Service Crashes Plan — 2026-05-24

## Purpose
Виправити 2 systemd сервіси які перебувають у crash-loop: `rt-whisper.service` (179 перезапусків, `float16` на CPU) та `rt-app.service` (555 перезапусків, `pactl` не знайдено).

## Scope
Мінімальні зміни в `flake-global/home-manager-module.nix`. Жодних змін Python-коду — проблеми виключно в конфігурації systemd unit-ів.

## Діагноз

### Проблема 1: `rt-whisper.service` — float16 на CPU

**Симптом:** `ValueError: Requested float16 compute type, but the target device or backend do not support efficient float16 computation.` — сервіс падає кожні ~7 сек, 179 перезапусків.

**Корінь:** `ExecStart` для `rt-whisper` в `home-manager-module.nix` (рядок 126) не передає `--compute-type`. У `whisper_service.py` (рядок 169) дефолтне значення — `"float16"`. На CPU `float16` не підтримується.

**Фікс:** Додати `--compute-type float32` до `ExecStart` `rt-whisper`.

### Проблема 2: `rt-app.service` — pactl не в PATH

**Симптом:** `pactl not found` попри те що `pactl` встановлений. 555 перезапусків.

**Корінь:** `rt-app` вже має `Environment = "PATH=%h/.nix-profile/bin:/run/wrappers/bin:/etc/profiles/per-user/%h/bin:..."`. `pactl` фізично лежить у `/etc/profiles/per-user/dmaslo/bin/pactl`. Проблема — `%h` розширюється в `/home/dmaslo`, тому шлях стає `/etc/profiles/per-user//home/dmaslo/bin` (битий). Для імені користувача треба `%u` (username).

**Фікс:** Замінити `%h` на `%u` у фрагменті `/etc/profiles/per-user/%h/bin` в PATH.

### Проблема 3: Дублікати процесів (як бонус)

На фоні висять 2 зайві процеси від старого Nix build (`1fiwps1swdabglakb0rj2pr5fywfkx9j`): `translator-hybrid-whisper` (PID 80066) та `translator-tts` (PID 80068). Вони не під management systemd і не будуть автоматично вбиті.

Після виправлення crash-loop-ів — вбити вручну: `kill 80066 80068`.

## Entry Points
- `flake-global/home-manager-module.nix` (рядки 118-138 — `rt-whisper`, рядок 175 — PATH `rt-app`)

## Planned Changes

### Change 1: Whisper compute_type
- [ ] File: `flake-global/home-manager-module.nix`, рядок 126
- [ ] Зміна: Додати `--compute-type float32`:
```nix
ExecStart = "${rtPackages.app}/bin/translator-whisper --socket-path %t/rt/rt-whisper.sock --compute-type float32";
```

### Change 2: rt-app PATH
- [ ] File: `flake-global/home-manager-module.nix`, рядок 175
- [ ] Зміна: `%h` → `%u`:
```nix
Environment = "PATH=%h/.nix-profile/bin:/run/wrappers/bin:/etc/profiles/per-user/%u/bin:/nix/var/nix/profiles/default/bin:/run/current-system/sw/bin";
```

### Change 3: Ручне вбивство дублікатів
- [ ] Після rebuild: `kill 80066 80068`

## Test Coverage
- [ ] `systemctl --user restart rt-whisper` — стартує без помилки `float16`
- [ ] `systemctl --user restart rt-app` — стартує без помилки `pactl not found`
- [ ] `journalctl --user -u rt-whisper -n 10` — лог показує `compute=float32`
- [ ] `systemctl --user show rt-whisper --property=NRestarts` — скинувся до 0
- [ ] `systemctl --user show rt-app --property=NRestarts` — скинувся до 0

## Dependencies
### Hard Dependencies
- needs: `nixos-rebuild switch` або `home-manager switch` для застосування змін

### Soft Dependencies
- after: переконатись що `pulseaudio` пакет є в `home.packages` (вже є — рядок 25)

## Architectural Invariants
- НЕ змінювати Python код — проблеми тільки в systemd конфігурації
- НЕ додавати CPUSchedulingPolicy (відомо що це ламає user services)
- НЕ міняти compute-type в Python коді — дефолт `float16` правильний для CUDA, а systemd передає `float32` для CPU

## Completion Criteria
- [ ] `rt-whisper.service` — active (running), 0 перезапусків
- [ ] `rt-app.service` — active (running), 0 перезапусків
- [ ] Жодних `pactl not found` в логах
- [ ] Жодних `float16` помилок в логах whisper
- [ ] Зайві процеси вбиті

## Post-Implementation Notes
(to be filled after implementation)
- Що пішло не за планом
- Якщо знадобились додаткові зміни
