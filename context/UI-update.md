src/ui/widgets/status_logger.py
Ось рекомендації, як інтегрувати цей `StatusLogger` / `StatusManager` у твою архітектуру для дебагу всіх сервісів (`capture`, `whisper`, `translate`, `tts`, `playback`):

---

## 1. Загальні принципи

1. **Центральний логгер на UI**
   * `StatusLogger` використовується тільки для dev/UI режиму.
   * Кожен сервіс надсилає повідомлення через окремий канал (можна IPC / socket / Queue) до UI, який викликає `add_log_message`.
2. **Дублювання в loguru**
   * `StatusManager` дублює логування у `loguru`, щоб можна було писати у файл або stdout без UI.
   * Кожен сервіс має свій `StatusManager`.
3. **Формат повідомлень**
   * `[HH:MM:SS] LEVEL: message`
   * Поділяти повідомлення на `INFO`, `WARNING`, `ERROR`, `DEBUG`.
4. **Ліміти**
   * UI тримає максимум 1000 повідомлень.
   * В internal list `_messages` теж обмеження 1000.

---

## 2. Інтеграція у сервіси

### a) rt-capture-service

```python
from status_logger import StatusManager

status = StatusManager()

status.set_status("Initializing capture device...")
status.log_info("Using device: Microphone 1")
try:
    # запис аудіо
    status.log_debug("Captured frame size=1024")
except Exception as e:
    status.log_error(f"Capture failed: {e}")
```

* Статус: короткі повідомлення про стан (готовність, запис)
* Логи: детальні дебаг-повідомлення про frames, errors

---

### b) rt-whisper-service

```python
status = StatusManager()

status.set_status("Loading ASR model...")
status.log_info("Model loaded: medium")
for segment in audio_segments:
    status.log_debug(f"Segment start={segment.start}, end={segment.end}")
    status.log_info(f"Recognized text: {segment.text}")
```

* Статус: модель завантажена, готовий до ASR
* Логи: сегменти, проміжні результати, помилки

---

### c) rt-translate-service

```python
status = StatusManager()

status.set_status("Translating text...")
status.log_info(f"Original text: {segment.text}")
status.log_info(f"Translated text: {translated.text}")
```

* Статус: поточний сегмент перекладено
* Логи: оригінальний і перекладений текст

---

### d) rt-tts-service

```python
status = StatusManager()

status.set_status("Synthesizing audio...")
status.log_debug(f"TTS segment length={len(segment.text)}")
status.log_info("TTS audio generated")
```

* Статус: поточний сегмент обробляється
* Логи: деталі синтезу, помилки

---

### e) rt-playback-service

```python
status = StatusManager()

status.set_status("Playing audio...")
status.log_debug(f"Playing chunk size={chunk_size}")
status.log_info("Playback finished")
```

* Статус: програвач активний / idle
* Логи: chunks, помилки відтворення

---

## 3. Архітектурні рекомендації

1. **Dev mode** (`nix develop`)
   * UI запускається у shellHook або вручну.
   * Сервіси пишуть у `StatusManager`, UI показує логи live.
   * PipeWire virtual sinks створюються у shellHook.
2. **Prod mode** (systemd)
   * UI не потрібен.
   * `StatusManager` логуватиме лише через loguru або у файл.
   * Socket-активація через systemd.
3. **IPC / повідомлення у UI**
   * Можна додати `asyncio.Queue` або `multiprocessing.Queue` для передачі логів у UI від сервісів, щоб не блокувати основний потік сервісу.

---

Якщо хочеш, можу підготувати  **готовий приклад інтеграції для всіх сервісів з asyncio Queue** , щоб логи потрапляли у UI live і в loguru одночасно.

Хочеш, щоб я це зробив?
