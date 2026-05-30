# TODO / Технічні нотатки

## GPU-перехід: torch/CUDA build проблеми

### Суть проблеми
При переході translator-ui на GPU, NixOS збирає ~1200+ пакетів через ланцюжок:
```
kokoro → torch (source build) → cuda12.9-libnvshmem (source build) → ~1200 пакетів
```
Будівля завалюється через SIGKILL (OOM або watchdog).

### Зроблено
- `system-conf/flake.nix`: overlay замінює `kokoro`'s torch на `torch-bin` (prebuilt PyPI wheel)
- `system-conf/machines/cyborg/configuration.nix`: додано `cuda-maintainers.cachix.org` як substituter

### Ключове відкриття
`torch-bin` в `nixpkgs-unstable` — це prebuilt wheel (`cu128` варіант), але nixpkgs ТАКОЖ
компілює `libnvshmem-3.6.5-0` з сорців як `buildInput` для патчінгу wheel через patchelf.

Тобто: навіть `torch-bin` вимагає компіляції `libnvshmem` з сорців у nixpkgs-unstable.

### Вирішення
Єдине що реально допомагає — бінарний кеш `cuda-maintainers.cachix.org` де `libnvshmem`
вже зібраний. Перший rebuild запускати з:
```bash
sudo nixos-rebuild switch --flake .#cyborg \
  --option substituters "https://cache.nixos.org https://cuda-maintainers.cachix.org" \
  --option trusted-public-keys "cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY= cuda-maintainers.cachix.org-1:0dq3bujKpuEPMCX6U4WylrUDZ9JyUG0VpVZa7CNfq5E="
```

### Потребує перевірки
- [ ] Чи `cuda-maintainers.cachix.org` справді містить `libnvshmem-3.6.5-0` для nixpkgs-unstable?
      Якщо ні — доведеться або компілювати (довго), або шукати альтернативу
- [ ] Перевірити `nix.settings.substituters` у `cyborg/configuration.nix` після успішного rebuild
- [ ] Чи overlay `kokoro.override { torch = torch-bin; }` дає реальний виграш якщо libnvshmem
      все одно компілюється? (Версія torch, можливо, важливіша для shmem-залежності)

### Альтернативи якщо cachix не допоможе
1. **Окремий Docker контейнер** для TTS/translator-ui з NVIDIA runtime — уникнути Nix build взагалі
2. **Пінувати nixpkgs на версію де torch-bin не має libnvshmem** (перевірити nixos-24.11)
3. **Замінити kokoro на piper-tts** — C++ TTS, без torch/CUDA, є в nixpkgs як `pkgs.piper-tts`
   - Потребує переписати TTS сервіс (`src/tts_service.py` або аналог)
   - Якість порівняна, але інший API і моделі ONNX
   - Повністю уникає torch у TTS pipeline

### Стан файлів після змін
- `system-conf/flake.nix`: overlay kokoro→torch-bin
- `system-conf/machines/cyborg/configuration.nix`: cuda-maintainers substituter
- `flake-global/flake.nix`: прибрано overlay (був там тимчасово)
- `flake-global/home-manager-module.nix`: прибрано pkgs.extend (був там тимчасово)
