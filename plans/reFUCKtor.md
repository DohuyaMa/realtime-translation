---
name: session-2026-05-23
description: "Стан на 2026-05-23: що зламано, що виправлено, що далі робити"
metadata:
  type: project
---

## Що зробили в цій сесії

### Виправлено
1. **Thread-safety UI оновлень** (`src/ui/widgets/main_window.py`)
   - Було: `QTimer.singleShot(0, lambda ...)` у `update_ui_from_controller` — мовчки не працює з non-Qt thread
   - Стало: `_update_signal = Signal(object)` як class attribute + `self._update_signal.emit(update_data)` — єдиний безпечний спосіб перейти з `threading.Thread` у Qt main thread
   - Підключення: `self._update_signal.connect(self._apply_ui_update)` в `__init__`

2. **Дублікат `addLayout`** в `init_ui` — `upper_layout.addLayout(status_layout)` викликався двічі поспіль, прибрано

### Ще НЕ виправлено / не перевірено

#### Головна проблема: моделі не завантажені
- `/home/dmaslo/.cache/transformers/` — **порожньо**
- `/home/dmaslo/.cache/huggingface/hub/` — є тільки `models--hexgrad--Kokoro-82M/` (TTS є ✓)
- **Відсутні:**
  - `Helsinki-NLP/opus-mt-uk-en` — потрібен для translate service (Ukrainian→English)
  - `Systran/faster-whisper-medium` — потрібен для whisper/hybrid-whisper service
- Без цих моделей translate та whisper сервіси крашаться при старті

#### Audio level постійно 0
- Причина 1: Signal fix тільки що застосований — до цього `_apply_ui_update` взагалі не викликався
- Причина 2: Pipeline треба запустити кнопкою "Start Translation" — тільки тоді `_pipeline_loop` стартує і аудіо рівень починає оновлюватись
- Причина 3: Можливо мікрофон/звукова карта не доступна без перевірки

#### Сервіси "мертві" в UI
- В **devShell режимі** (немає systemd socket файлів) — всі IPC клієнти = None
- `_devshell_started` set в `DirectAdapter` відстежує які сервіси "стартанули" через UI кнопки
- Реального процесу сервісу **не запускається** — тільки UI позначка
- Для реальної роботи: або запускати сервіси вручну в окремих терміналах, або systemd (production)

## Наступні кроки (пріоритет зверху вниз)

1. **Завантажити моделі** (без цього нічого не працює):
   ```bash
   # В nix develop shell:
   python3 -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8')"
   python3 -c "from transformers import pipeline; pipeline('translation', model='Helsinki-NLP/opus-mt-uk-en')"
   ```
   - Whisper `small` замість `medium` — менше VRAM (244MB vs 769MB), достатньо для тестування
   - Або зробити скрипт `scripts/download_models.py`

2. **Змінити default Whisper model** з `medium` на `small` або `base` в `whisper_service.py` та `hybrid_whisper_service.py` (рядок `default="medium"`)

3. **Перевірити Signal fix** — запустити UI, натиснути Start Translation, перевірити чи сервіси тепер зелені і чи audio level bar рухається

4. **Перевірити end-to-end pipeline**:
   - Запустити translate service: `python3 -m src.translate.translate_service`
   - Запустити hybrid-whisper service: `python3 -m src.whisper.hybrid_whisper_service`
   - Запустити TTS service: `python3 -m src.tts.tts_service`
   - Запустити UI: `python3 -m src.main`
   - Говорити українською → перевірити recognized + translated panels в UI

## Поточний стан файлів (що змінено і готово)

| Файл | Стан |
|---|---|
| `src/ui/widgets/main_window.py` | ✅ Signal fix + дублікат прибрано |
| `src/ui/widgets/status_logger.py` | ✅ Три панелі: recognized / translated / log |
| `src/translation_system.py` | ✅ sounddevice capture 48kHz→16kHz, WhisperSocketClient, pending text lists |
| `src/adapters/direct_adapter.py` | ✅ _devshell_started, audio levels fix, devShell mode detection |
| `src/audio/routing.py` | ✅ _closed guard проти double-close |
| `src/tts/tts_service.py` | ✅ synthesize_sync() замість synthesize() |
| `src/whisper/whisper_service.py` | ⚠️ default model = "medium" — треба змінити на "small" |
| `src/whisper/hybrid_whisper_service.py` | ⚠️ default model = "medium" — треба змінити на "small" |
| `src/translate/translate_service.py` | ⚠️ завантажує Helsinki-NLP/opus-mt-uk-en — модель відсутня в кеші |
| `src/status_logger.py` | ✅ component_name додано |
| `src/common/ipc.py` | ✅ client_id, таймінги, детальні помилки |
| `src/capture/capture_service.py` | ✅ PipeWire sinks логи, frame stats, dropped counters |
| `src/whisper/whisper_service.py` | ✅ таймінг моделі + транскрипції, RTFX, audio buffer |
| `src/whisper/hybrid_whisper_service.py` | ✅ те саме + Wyoming з'єднання |
| `src/whisper/wyoming_client.py` | ✅ retry-логіка, wire-level логи, disconnected detection |
| `src/translate/translate_service.py` | ✅ таймінг моделі + per-request, fallback logging |
| `src/tts/tts_service.py` | ✅ таймінг синтезу, stats |
| `src/playback/playback_service.py` | ✅ underrun detection, buffer stats |
| `src/translation_system.py` | ✅ pipeline cycle таймінги, service connectivity log, warning при offline сервісах |
| `src/adapters/direct_adapter.py` | ✅ preflight, reconfigure, start/stop з logger.exception() |

---

## Сесія 2: Логи для виявлення помилок

### Що зроблено

Додано повне логування в усі 11 файлів системи. Основні зміни:

1. **`component_name` у StatusManager** — кожен лог має префікс `[service_name]`, одразу видно який сервіс логує
2. **`logger.exception()` замість `logger.error()`** — в усіх except-блоках тепер виводиться повний traceback
3. **Таймінги операцій** — модель loading, transcription, translation, TTS synthesis, pipeline cycle — всі з реальними числами
4. **Діагностичні лічильники** — frames captured/dropped, chunks played, playback underruns, pipeline cycles/errors
5. **Connectivity warning** — при старті з усіма offline сервісами виводиться WARNING з інструкцією
6. **Розрізнення "whisper offline" vs "no speech"** — в _process_chunk тепер WARNING коли socket не знайдено, замість "no speech detected"

### Як тепер виглядають логи

Старт з offline сервісами:
```
Service connectivity: {'capture': False, 'whisper_socket': False, 'translate': False, 'tts': False, 'playback': False}
WARNING  Pipeline started but NO speech services connected — start services manually: ...
```

Pipeline цикл:
```
Pipeline chunk #1: 144000→48000 samples resampled in 0.5ms level=0.012
WARNING  Pipeline cycle #1: whisper socket not found at /tmp/rt-whisper.sock — no speech processing possible
```

Стоп:
```
Translation system stopped: 5 pipeline cycles, 0 text chunks, 5 errors, whisper=0.0s translate=0.0s tts=0.0s
```

Транскрипція з підключеним whisper:
```
[whisper] Transcribed 3.0s audio → 1 segments in 1.23s (RTFX=2.4x)
[whisper] Recognized text: Привіт, як справи?
```

### Що залишається зламано

- ✅ Логи тепер інформативні, помилки видно
- ❌ **Моделі не завантажені** — whisper + translate сервіси крашаться при старті без моделей
- ❌ **Audio level = 0** (потенційно) — Signal fix має виправити, але не перевірено
- ❌ **devShell mode** — сервіси реально не запускаються, тільки UI позначка

### Пріоритет далі

1. Завантажити моделі (див. команди вище)
2. Змінити whisper default model на "small"
3. Запустити end-to-end тест з ручним стартом сервісів
