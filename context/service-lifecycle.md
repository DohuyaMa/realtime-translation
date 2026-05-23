src/adapters/direct_adapter.py
Старт/Стоп тепер реально керує життєвим циклом сервісів (spawn/terminate subprocesses), а не тільки pipeline loop.

---

## Проблема

Кнопка "Start Translation" запускала тільки pipeline loop (sounddevice capture → IPC), але самі сервіси (whisper, translate, tts) треба було запускати вручну в окремих терміналах через `python -m src.whisper.whisper_service`. Кнопка "Stop Translation" зупиняла pipeline, але **не звільняла пам'ять** — моделі залишалися завантаженими в RAM.

---

## Рішення

`DirectAdapter` тепер spawn/terminate сервіси як subprocesses:

```
[Start Translation]
  → _ensure_essential_services()
      → перевіряє чи є socket файли (/tmp/rt-whisper.sock, /tmp/rt-translate.sock, /tmp/rt-tts.sock)
      → якщо нема — spawn субпроцес через subprocess.Popen:
          python -m src.whisper.whisper_service --socket-path /tmp/rt-whisper.sock
          python -m src.translate.translate_service --socket-path /tmp/rt-translate.sock
          python -m src.tts.tts_service --socket-path /tmp/rt-tts.sock
  → _wait_for_services(timeout=120s)
      → polling socket файлів кожні 0.5s
      → якщо timeout — warning (pipeline продовжує, сервіси підключаться коли з'являться)
  → TranslationSystem.start()  // pipeline loop

[Stop Translation]
  → TranslationSystem.stop()  // зупиняє pipeline loop, capture
  → _stop_subprocesses()
      → terminate() → wait(5s)
      → якщо не відповів → kill()
      → очищає _service_processes dict
```

---

## Змінені файли

| Файл | Зміни |
|---|---|
| `src/adapters/direct_adapter.py` | Додано: `import os, subprocess, sys, time`, `from ..core.runtime import get_runtime_config` |
| | Новий параметр `__init__`: `auto_spawn_services: bool = True` |
| | Нові поля: `_service_processes: Dict[str, subprocess.Popen]`, `_auto_spawn_services` |
| | Нові методи: `_spawn_service()`, `_ensure_essential_services()`, `_wait_for_services()`, `_stop_subprocesses()` |
| | Змінено: `start_pipeline()` → spawn + wait + start |
| | Змінено: `stop_pipeline()` → stop + terminate |
| | Змінено: `reconfigure_wyoming()` → використовує `self.stop_pipeline()`/`self.start_pipeline()` |
| | Змінено: `cleanup()` → `_stop_subprocesses()` перед `translation_system.cleanup()` |
| `tests/test_direct_adapter.py` | Додано `auto_spawn_services=False` в тест pipeline_control |

---

## Деталі

### _spawn_service(name, module, args)
```
cmd = [sys.executable, '-m', module] + args
process = subprocess.Popen(cmd)
self._service_processes[name] = process
```
- stdout/stderr наслідуються від батьківського процесу (логи сервісів видно в тому ж терміналі)
- Якщо socket файл вже існує (сервіс запущений вручну або через systemd) — spawn не відбувається

### _ensure_essential_services()
- whisper: якщо `use_wyoming=True` → hybrid_whisper_service з `--use-wyoming`
- translate: translate_service
- tts: tts_service
- capture/playback не spawn — pipeline loop використовує sounddevice напряму

### _wait_for_services(timeout=120.0)
- Чекає тільки на socket файли тих сервісів, які були заспавнені
- 120s тому що моделі (особливо whisper medium) завантажуються довго
- Timeout не фатальний — pipeline loop працює, сервіси підключаються коли готові

### _stop_subprocesses()
- graceful terminate (SIGTERM) → 5s wait
- force kill (SIGKILL) якщо timeout
- очищає `_service_processes`

### auto_spawn_services=False
- Використовується в тестах, щоб `start_pipeline()` не блокувався на 120s
- В production systemd mode теж можна вимкнути (сервісами керує systemd)
- `main.py` не передає цей параметр, тому default=True

---

## Поведінка в різних режимах

### devShell (nix develop)
- `auto_spawn_services=True` (default)
- Start → spawn 3 субпроцеси, чекає socket, запускає pipeline
- Stop → terminate субпроцеси → RAM звільнено
- Якщо сервіси вже запущені вручну в інших терміналах — socket файли існують, spawn не відбувається

### Production (systemd)
- Сервісами керує systemd через socket activation
- Socket файли існують (`$XDG_RUNTIME_DIR/rt-*.sock`), spawn не відбувається
- Можна передати `auto_spawn_services=False` в `main.py` для явного вимкнення

### Тести
- `auto_spawn_services=False`
- start_pipeline() не spawn сервіси — pipeline loop стартує, намагається підключитися, фейлиться gracefully

---

# Session 3 — Багі та Nix fixes

## Знайдені помилки при першому запуску

### 1. translate_service: self.status before assignment
`_initialize_model()` (line 38) викликав `self.status.log_info()` до того як `self.status = StatusManager(...)` створено (line 51). Баг виправлено — `self.status` перенесено перед `_initialize_model()`.

**Файл**: `src/translate/translate_service.py`

### 2. spaCy `en_core_web_sm` pip install
```
KPipeline.__init__() → misaki/en.py G2P.__init__() →
spacy.cli.download('en_core_web_sm') →
subprocess.run(['pip', 'install', 'en_core_web_sm-3.8.0-py3-none-any.whl'])
→ ERROR: externally-managed-environment
```
spaCy намагається `pip install` модель NLP токенізації. В Nix це падає.

**Рішення**: Додано `en_core_web_sm` як Nix package wheel у flake.

**Файли**:
- `flake-global/dev/devshell.nix` — додано в `let` + `withPackages`
- `flake-global/parts/python/tts.nix` — додано в `dependencies`

### 3. Wyoming server не запущено
`use_wyoming=true` в конфігу, але Wyoming сервер не працює на localhost:10300. Якщо не плануєш використовувати Wyoming, вимкни в конфігу.

---

## Моделі (Nix-style)

| Модель | Тип | Статус | Шлях в Nix |
|---|---|---|---|
| **Helsinki-NLP/opus-mt-uk-en** | HuggingFace transformer | ❌ Потрібно скачати | `~/real-time-translator-cache/huggingface/hub/` |
| **hexgrad/Kokoro-82M** | Kokoro TTS | ✅ Вже є | `~/.cache/huggingface/hub/` |
| **en_core_web_sm** | spaCy NLP model | ✅ Додано в flake | Nix store (wheel) |
| **faster-whisper** | Whisper ASR | ⏸ Wyoming mode | `~/.cache/whisper/` (якщо вимкнути Wyoming) |

**Як скачати перекладну модель через Nix**:
```bash
nix develop --command python3 -c "
from transformers import pipeline
pipeline('translation', model='Helsinki-NLP/opus-mt-uk-en')
"
```
