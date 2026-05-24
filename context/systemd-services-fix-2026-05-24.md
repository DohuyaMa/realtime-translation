# Systemd Services Fix — 2026-05-24

## Поточний стан (що зараз працює)

### Сервіси
Всі 5 сервісів запускаються як systemd user units і стабільно тримаються:

```
● rt-capture.service    — active (running)
● rt-playback.service   — active (running)
● rt-translate.service  — active (running)
● rt-tts.service        — active (running)
● rt-hybrid-whisper.service — active (running)  ← Wyoming → localhost:10300
```

Сокети живуть в `/run/user/1000/rt/`:
```
rt-capture.sock
rt-hybrid-whisper.sock
rt-playback.sock
rt-tts.sock
rt-translate.sock
```

### Як запустити translator-ui
З термінала або через `rt-app.service` (graphical-session.target):
```bash
translator-ui
```

Або через systemd:
```bash
systemctl --user start rt-app
```

### HF Token
Токен читається з файлу (НЕ захардкоджений в коді):
```bash
~/.config/real-time-translator/.hf_token
```
Файл має містити лише один рядок — сам токен, без пробілів. Права: `chmod 600`.

---

## Що було зламано і як було виправлено

### 1. `CPUSchedulingPolicy=rr` — SIGABRT / status 214/SETSCHEDULER

**Симптом:** Всі 6 сервісів крашились одразу при старті з `status=214/SETSCHEDULER` і перезапускались 160+ разів.

**Причина:** `CPUSchedulingPolicy=rr` (real-time round-robin scheduling) потребує `CAP_SYS_NICE`. User-level systemd сервіси не мають цього capability.

**Виправлення:** Видалено `CPUSchedulingPolicy = "rr"` з усіх 6 service units у `home-manager-module.nix`. Залишено `Nice = -5` і `IOSchedulingClass = "best-effort"` — вони дозволені для user units.

---

### 2. `/run/user/1000/rt/` зникав між рестартами

**Симптом:** `os.path.exists(socket_path)` повертав False навіть коли subprocess стверджував що сокет є. Директорія то з'являлась, то зникала.

**Причина:** `RuntimeDirectory = "rt"` у service units створює і **видаляє** директорію при кожному старті/стопі сервісу. Оскільки сервіси крашились (через п. 1), директорія постійно перестворювалась і зникала. Socket units не мають власного `RuntimeDirectory`, тому не могли створити директорію до запуску.

**Виправлення:**
- Додано `RuntimeDirectoryPreserve = "yes"` до всіх service units — директорія не видаляється при зупинці сервісу.
- Додано `systemd.user.tmpfiles.rules = [ "d %t/rt 0700 - - -" ]` в home-manager-module — директорія гарантовано існує ще до старту будь-якого socket unit.

---

### 3. Бінарники `rt-*-service` не існували

**Симптом:** `status=203/EXEC` — "No such file or directory" для `rt-capture-service`, `rt-whisper-hybrid-service` тощо.

**Причина:** В `pyproject.toml` entry points названі `translator-*` (наприклад `translator-capture`), а в `home-manager-module.nix` ExecStart використовував старі назви `rt-capture-service` (назви які ніколи не існували у проекті).

**Виправлення:** Всі `ExecStart` виправлені на правильні бінарники:
```
rt-capture-service        → translator-capture
rt-playback-service       → translator-playback
rt-translate-service      → translator-translate
rt-tts-service            → translator-tts
rt-whisper-service        → translator-whisper
rt-whisper-hybrid-service → translator-hybrid-whisper
```

---

### 4. Пакети не збирались при `nixos-rebuild switch`

**Симптом:** Service files референсували store paths (наприклад `/nix/store/nks15v7z.../`) які не існували після rebuild.

**Причина:** `home-manager-module.nix` використовує `import ./prod/packages.nix { inherit pkgs lib; }` — тобто пакети збираються з `pkgs` системи (swarm-nix). String interpolation `${rtPackages.app}` в ExecStart теоретично має тягнути пакет як залежність, але на практиці per-service пакети (`rt-capture-service`, `rt-translate-service` тощо) не потрапляли в build closure при `nixos-rebuild switch`.

**Виправлення:** Всі service units тепер посилаються на **один** пакет — `rtPackages.app` (UI пакет), який вже є в `home.packages` і гарантовано збирається:

```nix
ExecStart = "${rtPackages.app}/bin/translator-capture --socket-path %t/rt/rt-capture.sock";
ExecStart = "${rtPackages.app}/bin/translator-hybrid-whisper --socket-path %t/rt/rt-hybrid-whisper.sock ...";
# і так далі для всіх сервісів
```

Це працює тому що `buildPythonApplication` з нашого `pyproject.toml` встановлює **всі** 7 entry points в кожен пакет (вони всі зібрані з одного `src/`). Різниця між `capturePackage`, `translatePackage` тощо — лише Python залежності, але бінарники однакові.

---

### 5. `CPUSchedulingPolicy=rr` конфлікт з PYTHONPATH у subprocess

**Симптом:** При запуску `translator-ui` з термінала (не через systemd), subprocess spawning (`_ensure_essential_services`) не міг знайти модуль `src` → `ModuleNotFoundError`.

**Причина:** Nix обертає скрипти через shell wrapper який встановлює `PYTHONPATH`. При `subprocess.Popen(cmd)` без явного `env`, дочірній процес отримує лише `sys.executable` (голий Python без Nix PYTHONPATH). При використанні entry-point скриптів через `sys.argv[0]` батьківський PYTHONPATH не передавався.

**Виправлення** (у `direct_adapter.py`):
- `_resolve_cmd()` — шукає entry-point скрипти в тій же bin/ директорії де лежить `translator-ui`, потім `shutil.which`, fallback `python -m`.
- `_spawn_service()` — явно копіює `sys.path` в `PYTHONPATH` env var для дочірніх процесів.

---

### 6. `transformers v5.5` — `"translation"` pipeline task видалено

**Симптом:** `Unknown task translation` при запуску translate сервісу.

**Причина:** transformers v5 видалив pipeline task `"translation"`. `AutoTokenizer.from_pretrained()` також не розпізнає `MarianConfig`.

**Виправлення** (`translate_service.py`): Пряме використання `MarianTokenizer` + `MarianMTModel` без Auto-класів:
```python
from transformers import MarianTokenizer, MarianMTModel
self._tokenizer = MarianTokenizer.from_pretrained(model_name)
self._model = MarianMTModel.from_pretrained(model_name).to(self._device)
```

---

### 7. TTS subprocess вбивався через `sys.exit()` від spaCy

**Симптом:** TTS сервіс стартував, але виходив з кодом `1` до того як створити сокет. `120s timeout` в `_wait_for_services`.

**Причина:** Kokoro → misaki → `spacy.load('en_core_web_sm')` → spaCy викликає `sys.exit(1)` якщо модель не знайдено. `except Exception` не ловить `SystemExit` (це `BaseException`).

**Виправлення** (`tts_engine.py`):
```python
except BaseException as e:
    if isinstance(e, KeyboardInterrupt):
        raise
    logger.warning(f"TTS engine unavailable ...")
```

TTS працює в "silent mode" без Kokoro якщо spaCy не ініціалізувалась. `espeak-ng` додано в `home.packages` як fallback.

---

### 8. `rt-app.service` крашився (Qt platform SIGABRT)

**Симптом:** `translator-ui` як systemd сервіс падав з SIGABRT — Qt не міг знайти Wayland/X11 display.

**Причина:** Сервіс був `WantedBy=default.target` — стартував до того як KDE Plasma встановила `WAYLAND_DISPLAY` в user systemd environment.

**Виправлення:**
```nix
Unit.After = [ "graphical-session.target" ... ];
Unit.PartOf = [ "graphical-session.target" ];
Install.WantedBy = [ "graphical-session.target" ];
Service.PassEnvironment = "WAYLAND_DISPLAY DISPLAY XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS QT_QPA_PLATFORM";
```

---

## Чого НЕ варто робити

### ❌ Не додавати `CPUSchedulingPolicy = "rr"` у user services
User systemd units не мають `CAP_SYS_NICE`. Це миттєво кладе сервіс з кодом 214/SETSCHEDULER.  
Якщо real-time scheduling справді потрібен — треба NixOS kernel parameter або capabilities через `AmbientCapabilities=CAP_SYS_NICE` (але це security risk).

### ❌ Не додавати кілька пакетів з однаковими бінарниками в `home.packages`
Всі rt-* пакети (capture, playback, translate, tts, whisper, hybrid-whisper) встановлюють **ідентичний набір** з 7 бінарників (`translator-*`). `buildEnv` кидає конфлікт при спробі додати більше одного. Використовуй лише `rtPackages.app`.

### ❌ Не використовувати per-service пакети в ExecStart без перевірки що вони є в build closure
Per-service пакети (`capturePackage`, `translatePackage` тощо) не потрапляють автоматично в build closure при `nixos-rebuild switch` через особливості оцінки home-manager модуля. Якщо їх немає в `home.packages`, store path буде правильним але пакет не збудується → `203/EXEC` при старті сервісу.

### ❌ Не хардкодити HF_TOKEN в коді
Токен завантажується з `~/.config/real-time-translator/.hf_token`. Файл не в репозиторії. Нічого що схоже на токен не повинно з'явитись в git history.

### ❌ Не використовувати `except Exception` для ловлення `sys.exit()`
`SystemExit` є `BaseException`, не `Exception`. Потрібен `except BaseException` (з re-raise для `KeyboardInterrupt`).

### ❌ Не ставити `WantedBy=default.target` для GUI сервісів
GUI сервіси (все що використовує Wayland/X11) мають бути `WantedBy=graphical-session.target` + `PartOf=graphical-session.target`. Інакше Qt/GTK падає до того як display готовий.

### ❌ Не забувати `RuntimeDirectory` на socket units або `tmpfiles.d`
Socket units намагаються створити сокет файл в директорії яка ще не існує. Або додай `tmpfiles.d` правило, або переконайся що директорія вже є до старту sockets.target.

---

## Файли які змінювались

| Файл | Що змінено |
|------|-----------|
| `flake-global/home-manager-module.nix` | Видалено `CPUSchedulingPolicy=rr`, виправлено бінарники на `translator-*`, всі ExecStart через `rtPackages.app`, додано `RuntimeDirectoryPreserve=yes`, додано `tmpfiles.d`, виправлено socket paths `%t/rt/`, виправлено `rt-app.service` для graphical-session |
| `src/adapters/direct_adapter.py` | `_resolve_cmd()`, `_socket_is_live()`, `_need_spawn()`, `_spawn_service()` з PYTHONPATH propagation |
| `src/translate/translate_service.py` | `MarianTokenizer` + `MarianMTModel` замість Auto API |
| `src/models/tts_engine.py` | `except BaseException` для ловлення spaCy `sys.exit()` |
| `src/core/env.py` | HF token завантаження з `~/.config/real-time-translator/.hf_token` |
| `flake-global/parts/python/ui.nix` | Додано `sentencepiece` і `sacremoses` (потрібні `MarianTokenizer` в subprocess) |

---

## Архітектура сокетів (production)

```
systemd socket unit (sockets.target)
    └── ListenStream = %t/rt/rt-*.sock  (/run/user/1000/rt/)
            ↓ (socket activation — при першому з'єднанні)
systemd service unit
    └── ExecStart = ${rtPackages.app}/bin/translator-* --socket-path %t/rt/rt-*.sock

translator-ui (rt-app.service або з термінала)
    ├── DirectAdapter._socket_is_live() → перевіряє чи сокет живий
    ├── якщо живий → підключається напряму (production mode)
    └── якщо мертвий → _spawn_service() → subprocess (fallback/dev mode)
```

## Архітектура Wyoming (whisper)

```
wyoming-faster-whisper  (окремий сервіс, localhost:10300)
    ↑
translator-hybrid-whisper  (rt-hybrid-whisper.service)
    ← підключається до Wyoming при старті
    ← слухає на /run/user/1000/rt/rt-hybrid-whisper.sock
    ↑
translation_system.py  (WhisperSocketClient)
    ← відкриває нове з'єднання на кожен chunk аудіо
```
