# Стрімінгові ASR і Vosk → одночасний переклад (2026-10-31)

Власник: «Від початку я говорив, що whisper має працювати в режимі стрімінгу —
це значно пришвидшить обробку, Vosk теж має так само працювати, Qwen має
отримувати цю інформацію і розуміти контекст до і як далі перекласти.» Плюс:
«Коли натискається Start Translation — все має прогріватись.»

Протокол уже описаний у
[`2026-10-06-speech-services-api-design.md`](2026-10-06-speech-services-api-design.md)
§6.2–6.4; LocalAgreement-2 там був «P2, на майбутнє» — тут він основний.
Наявний каркас: `ws_routers` (`src/services/common/app.py`), `ws_authorized`
(`src/services/common/auth.py`), `Priority.FINAL/PARTIAL`
(`src/services/common/worker.py`), `websockets` у `nix/packages/deps.nix` і
`edge-deps.nix`.

## Рішення власника (дефолти, прийняті 2026-10-31)

1. Одиниця перекладу: кінець речення / endpoint (тиша) / ≥ 12 неперекладених
   слів на межі клаузи (`,;:`) / жорстко 20 слів.
2. Незафіксований хвіст показується в UI сірим, не озвучується.
3. Вже озвучений переклад не виправляється.
4. Режим transcribe теж переходить на стрім; паузний сегментатор видаляється.
5. `auto`-мова: визначається на першій репліці й фіксується на сесію.
6. Whisper turbo `int8_float16`, тік 500 мс, endpoint 500 мс тиші.
7. Без пріоритету GPU між whisper і LLM (тіки latest-wins самі пригальмовуються).

## 1. Whisper — LocalAgreement-2

`StreamSession` (`src/services/asr/stream.py`, чистий клас): Silero VAD з
faster-whisper; кожні `RT_ASR_STREAM_TICK_MS` (500) перекодує буфер мовлення,
що росте (`beam_size=1`, `temperature=0` без fallback, `word_timestamps=True`,
`condition_on_previous_text=False`, `initial_prompt` = зафіксований текст
≤ 200 символів). Фіксує найдовший спільний префікс двох останніх гіпотез
(нормалізовані слова, пунктуація з новішої). Буфер обрізається по кінцю
зафіксованого речення, при > 15 с — по кінцю сегмента. VAD-тиша ≥ 500 мс
(endpoint) фіксує всю гіпотезу. `SegmentFilter`/`filter_repetitions` — на
кожну гіпотезу; перевірка echo-повтору prompt — на сервері. SimulStreaming
відкинуто: потребує cross-attention (немає в CTranslate2 → torch-whisper,
+~3 ГБ VRAM, немає в nixpkgs). Оцінка: 150–250 мс на тік turbo; тіки
latest-wins (пропуск, поки попередній рахується). Затримка фіксації ≈ 1.0–1.5 с
посеред мови, ≈ 0.7 с на паузі.

## 2. Vosk у стрімінгу

`KaldiRecognizer` на WS-сесію (`SetWords`, `SetPartialWords` якщо є у версії):
`AcceptWaveform` → `final` (слова, conf, start, end) або `partial` (≤ 1/300 мс,
лише зміни). Edge шле ті самі кадри в обидва сервіси, час — семплами від
`start` → спільна шкала. Підказка для зафіксованого відрізка [t0, t1] — слова
Vosk із серединою в [t0−0.25, t1+0.25] (finals; для хвоста partial words).

## 3. Протокол `WS /v1/audio/stream` (asr і vosk)

Клієнт: `start{language, sample_rate:16000, prompt?, t0}`, binary PCM16LE по
100 мс, `stop`. Сервер: `ready{session_id, model}`, `final{seq, text, words,
start, end, eos}` (лише дописується), `partial{text, start}` (замінює хвіст),
`endpoint{at}`, `error{code, state}`, `done`; кожне несе `audio_end`.
Токен у handshake (невірний → close 1008); модель не готова →
`error{model_not_ready}` + 1013 (лінивий load як `runtime.acquire`); відкрита
сесія блокує idle unload; unload/RELOAD → `error model_unloaded` + 1012; ліміт
`RT_*_MAX_SESSIONS` → 1013. Edge: черга відправки ≤ 5 с аудіо, переповнення =
обрив; failover через pool (`_mark_down`/`pick`) з повтором кільцевого буфера
від кінця останнього зафіксованого слова, дублікати відкидаються за `end`.
Рубильник: `stop` → `done` ≤ 1 с → close → `/model/unload`.

## 3a. Прогрів на «Start Translation»

Паралельно: обидва WS (handshake вантажить модель; вивантажена →
`POST /model/load` + чекати `ready`; сервер після `ready` робить фіктивний
декод 1 с тиші), прогрів LLM і TTS. Захоплення стартує одразу, аудіо — у
кільцевий буфер (≤ 30 с) до готовності WS. UI: «прогрів…» з прапорцем на
кожен сервіс; тайм-аут/помилка видно явно. Лог: час від натискання до
повної готовності.

## 4. Edge — один механізм

`src/clients/stream.py` (WS-клієнт, потік, failover). `src/core/simul.py`:
`SourceLedger` (лише дописується) + `next_unit()` (правила з рішення 1).
LLM: контекст — попередні пари source→target; user: `TEXT:` одиниця, `HINT:`
відрізок Vosk, `NEXT (do not translate):` решта зафіксованого + partial;
system prompt: перекладати лише TEXT, NEXT — тільки для розуміння,
продовжувати попередній переклад узгоджено; `reject_reason` ловить витік NEXT.
Гейт Vosk — на одиницю (чекати покриття t1 ≤ 300 мс, інакше whisper-only,
видно в лозі); відкинута одиниця лише зсуває ledger. TTS/відтворення без змін;
`_Phrase` → `_Unit`; без повторної озвучки (одиниці не перетинаються).

## 5. Бюджет, вимірювання, міграція

Зараз: кінець мови → звук p50 3.2 с, p90 9.3 с. Очікування (не виміряно):
p50 ≈ 2.5 с, p90 ≤ ~4 с. Вимірювати: `commit_lag`, мс/тік, пропущені тіки,
`behind_s`, покриття Vosk, рішення гейта, LLM/TTS first, кінець слова → звук
p50/p90, backlog відтворення, перепідключення, WER офлайн-повтором сесій.

Кроки (кожен — зелений повний набір тестів):
- **S1** rt-asr WS (`stream.py`, `ws.py`, налаштування, `/status.sessions`);
  перевірка — повтор WAV: фіксації проти HTTP-транскрипту, лаг.
- **S2** rt-vosk WS; перевірка — середня |Δt| збіжних слів < 150 мс.
- **S3** edge-клієнт, офлайн-стенд, прогрів.
- **S4** перемикання конвеєра + в тому ж коміті видалення: `_held*`,
  `_route_text`, `_release_held*`, `_flush_held`, `_hold_*`, `HOLD_POLL_S`,
  `_enqueue_segment`, `_recognize`, `_vosk_submit/_result`, `_is_echo`,
  лічильники `asr_merged`/`asr_held_flushed` (+ UI), `whisper_word_times`,
  `Aligned.build` (→ `from_words`), відповідні тести.
- **S5** transcribe на стрім; видалити `segmentation/pause.py`, `chunks.py`,
  `audio/vad_stream.py` (рівень сигналу — простий RMS), ключі `SegmenterConfig`.

HTTP `/v1/audio/transcriptions` лишається для файлів і офлайн-оцінки; з edge
не викликається. Документацію (`docs/pipeline-diagrams.md`, `USAGE.md`)
оновлювати в кожному кроці.

## Стан на 2026-10-31 і що далі

Зроблено (не задеплоєно): S1 ec1efce, 9df1296; S2 a3064b0; S3 3cb5922;
S4 c1c42d4 + d41a398 (затримка), 6b833f1 (рівень входу, «Мікрофон тихий»,
Vosk «не чує»), 755f33b (документація), 0697e69 (live test §13);
S5 638f3a4 (паузний сегментатор, `segmentation.*`, `{{previous_text}}`
видалено).

Виміряно (`scripts.pipeline_bench`, приватні сервіси, Piper uk, природний
темп 215 с): кінець мови → відтворення p50 4.31 → 3.3–3.6 с, p90
7.65 → 5.6–6.3 с; швидкий темп (169 с): 6.38/11.38 → 4.23/8.12 с. Власник:
досить, тюнінг затримки зупинено. Складові p50: фіксація whisper ~1.05 с,
очікування Vosk 0.3 с, LLM ~0.6 с, TTS ~0.25 с + 0.19 с на секунду аудіо.

Далі:
- ціль 2.5/4.5 с не досягнута. Ідеї: потоковий `/v1/chat/completions` у
  LLM-воркері swarm-nix (зараз ігнорує `stream: true`, перша частина
  перекладу = уся відповідь); Vosk відстає від whisper на 0.5–2 с — HINT
  часто без останніх слів, очікування 300 мс допомагає лише коли приходить
  final; перша частина TTS для довгого речення без коми;
- живий тест тихого мікрофона: стенд (чистий Piper) відтворює провал лише
  на −52 dBFS; пороги −38 dBFS / +24 дБ перевірити на справжньому мікрофоні;
- виправлення онсету rt-asr (d41a398) потребує деплою rt-asr, не лише edge;
- ризик: старі `config.yml` з `{{previous_text}}` у `services.asr.prompt` —
  помилка збереження (явна), а не мовчазний ігнор.
