Добре. Пропоную зробити  **чіткий контракт для кожного сервісу** . Мета: щоб кожен модуль був  **ізольований, взаємодіяв через сокети, мав чіткий формат вводу/виводу** , і його можна було легко замінити.

---

# 1. rt-capture-service

### Призначення

Записує аудіо з мікрофона та відправляє його в pipeline через сокет.

### Контракт

**Вхід:** hardware microphone (PulseAudio / PipeWire)

**Вихід:** PCM 16-bit 16kHz через UNIX socket

```json
{
  "raw_pcm": "<байти WAV або raw PCM>"
}
```

**Параметри запуску:**

```
--socket-path /run/user/1000/rt-capture.sock
--energy-threshold 1000
--record-timeout 2
--phrase-timeout 3
```

**Вимоги:**

* thread-safe queue
* обробка пауз між фразами
* відокремлений logger (loguru)
* не має робити ASR/Translation/TTS

---

# 2. rt-whisper-service

### Призначення

Перетворює PCM аудіо у текст (ASR) за допомогою faster-whisper.

### Контракт

**Вхід:** PCM через сокет від `rt-capture-service`

```json
{
  "pcm": "<raw PCM 16kHz>"
}
```

**Вихід:** JSON сегментів

```json
{
  "segments": [
    {
      "start": 0.0,
      "end": 1.2,
      "text": "Hello",
      "final": true
    },
    ...
  ]
}
```

**Параметри запуску:**

```
--socket-path /run/user/1000/rt-whisper.sock
--model medium
--device cuda
--compute-type int8
```

**Вимоги:**

* faster-whisper, ctranslate2
* окремий pythonEnv
* логування результатів
* не має робити переклад або TTS
* можна робити буферизацію фраз (phrase_timeout)

---

# 3. rt-translate-service

### Призначення

Переклад тексту (ASR → target language)

**Вхід:** сегменти від `rt-whisper-service`

```json
{
  "segments": [
    {"text": "Hello", "start": 0.0, "end": 1.2, "final": true}
  ],
  "target_language": "ukrainian"
}
```

**Вихід:**

```json
{
  "segments": [
    {"text": "Привіт", "start": 0.0, "end": 1.2, "final": true}
  ]
}
```

**Параметри:**

```
--socket-path /run/user/1000/rt-translate.sock
```

**Вимоги:**

* локальний перекладач (Argos Translate)
* окремий pythonEnv
* не робити TTS або capture

---

# 4. rt-tts-service

### Призначення

Генерує аудіо (TTS) з тексту

**Вхід:** сегменти від `rt-translate-service`

```json
{
  "segments": [
    {"text": "Привіт", "start": 0.0, "end": 1.2, "final": true}
  ]
}
```

**Вихід:** PCM (16kHz) через сокет для `rt-playback-service`

```json
{
  "raw_pcm": "<байти WAV або raw PCM>"
}
```

**Параметри:**

```
--socket-path /run/user/1000/rt-tts.sock
```

**Вимоги:**

* kokoro / onnxruntime
* окремий pythonEnv
* буферизація для уникнення рваного аудіо
* логування

---

# 5. rt-playback-service

### Призначення

Приймає PCM аудіо і відтворює через PipeWire

**Вхід:** PCM від `rt-tts-service`

```json
{
  "raw_pcm": "<байти WAV>"
}
```

**Вихід:** відтворене аудіо, логування тільки для статусу

**Параметри:**

```
--socket-path /run/user/1000/rt-playback.sock
```

**Вимоги:**

* thread-safe queue
* підтримка PipeWire null-sink / virtual sink
* виключно відтворення, не робити ASR або TTS

---

# 6. UI (тестування / вибір device / live view)

### Контракт

* тільки клієнт
* читає сокети всіх сервісів
* показує PCM waveform + ASR + Translation + TTS status
* не робить власний capture або ASR

---

# 7. Принципи взаємодії

1. **Сокети** — єдиний канал між сервісами
2. **JSON** — формат для сегментів тексту
3. **Відокремлення обов’язків** — жоден сервіс не робить більше ніж його контракт
4. **PythonEnv** — кожен сервіс має свій оточуючий пакет, щоб уникнути конфліктів
5. **Dev vs Prod** :

* Dev → запуск після `nix develop` (shellHook + manual start)
* Prod → systemd (socket activation)

---

Якщо хочеш, можу  **намалювати блок-схему пайплайну з сокетами, JSON і env для кожного сервісу** , щоб одразу стало наочно і для документації flake.

Хочеш, щоб я це зробив?
